"""Formula language: tokenizer and recursive-descent parser. Produces an AST; never evaluates.

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/') unary)*
    unary   := '-' unary | postfix
    postfix := primary ('[' '-' INT ('q' | 'y') ']')?
    primary := NUMBER | ref | FUNC '(' expr ')' | '(' expr ')'
    ref     := METRIC | TICKER '.' METRIC
    FUNC    := ttm | abs

Positions are 0-based character offsets into the source; `length` is the span to underline.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

MAX_LENGTH = 500
MAX_DEPTH = 24       # nested parentheses, functions and unary minus
MAX_NODES = 100
MAX_LAG_QUARTERS = 40
FUNCTIONS = ("ttm", "abs")

_NUMBER = re.compile(r"\d{1,30}(?:\.\d{1,30})?|\.\d{1,30}")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_PUNCT = "+-*/()[]."


class FormulaError(Exception):
    """A problem at `pos` (length `length`) with a message and an optional hint."""

    def __init__(self, message: str, pos: int, length: int = 1, hint: str | None = None,
                 code: str = "syntax") -> None:
        super().__init__(message)
        self.message, self.pos, self.length, self.hint, self.code = (
            message, pos, max(1, length), hint, code)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "pos": self.pos,
                "length": self.length, "hint": self.hint}


@dataclass(frozen=True)
class Node:
    pos: int
    length: int


@dataclass(frozen=True)
class Num(Node):
    value: Decimal


@dataclass(frozen=True)
class Ref(Node):
    metric: str               # lower case
    ticker: str | None        # upper case; None = the default company
    metric_pos: int
    ticker_length: int = 0    # the ticker starts at pos


@dataclass(frozen=True)
class Lag(Node):
    expr: Node
    quarters: int             # [-1y] is 4


@dataclass(frozen=True)
class Call(Node):
    name: str
    arg: Node


@dataclass(frozen=True)
class Neg(Node):
    arg: Node


@dataclass(frozen=True)
class BinOp(Node):
    op: str
    left: Node
    right: Node
    op_pos: int


@dataclass(frozen=True)
class Token:
    kind: str                 # num, id, eof, or the punctuation character
    text: str
    pos: int


def tokenize(src: str) -> list[Token]:
    toks: list[Token] = []
    i = 0
    while i < len(src):
        c = src[i]
        if c.isspace():
            i += 1
        elif m := _NUMBER.match(src, i):
            if m.end() < len(src) and src[m.end()].isdigit():
                raise FormulaError("Number is too long", i, m.end() - i + 1)
            toks.append(Token("num", m.group(), i))
            i = m.end()
        elif m := _IDENT.match(src, i):
            if m.end() < len(src) and (src[m.end()].isalnum() or src[m.end()] == "_"):
                raise FormulaError("Name is too long", i, m.end() - i + 1)
            toks.append(Token("id", m.group(), i))
            i = m.end()
        elif c in _PUNCT:
            toks.append(Token(c, c, i))
            i += 1
        elif c == "%":
            raise FormulaError("Write percentages as ratios: 0.25, not 25%", i)
        else:
            raise FormulaError(f"Unexpected character {c!r}", i)
    toks.append(Token("eof", "", len(src)))
    return toks


def parse(src: str) -> Node:
    if len(src) > MAX_LENGTH:
        raise FormulaError(f"Formula is longer than {MAX_LENGTH} characters", MAX_LENGTH,
                           len(src) - MAX_LENGTH, code="limit")
    if not src.strip():
        raise FormulaError("Type a formula, e.g. net_income / revenue", 0)
    return _Parser(tokenize(src)).parse()


class _Parser:
    def __init__(self, toks: list[Token]) -> None:
        self.toks, self.k, self.depth, self.nodes = toks, 0, 0, 0

    def peek(self) -> Token:
        return self.toks[self.k]

    def next(self) -> Token:
        tok = self.toks[self.k]
        if tok.kind != "eof":
            self.k += 1
        return tok

    def end_of(self, tok: Token) -> int:
        return tok.pos + max(1, len(tok.text))

    def node(self, n: Node) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormulaError(f"Formula has more than {MAX_NODES} parts", n.pos, n.length,
                               code="limit")
        return n

    def enter(self, tok: Token) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError(f"Formula nests more than {MAX_DEPTH} levels deep", tok.pos,
                               code="limit")

    def parse(self) -> Node:
        n = self.expr()
        end = self.peek()
        if end.kind != "eof":
            if end.kind in ("id", "num", "("):
                raise FormulaError("Missing operator", end.pos, len(end.text),
                                   "Put + - * or / between terms")
            raise FormulaError(f"Unexpected '{end.text}'", end.pos)
        return n

    def expr(self) -> Node:
        return self.binary(self.term, "+-")

    def term(self) -> Node:
        return self.binary(self.unary, "*/")

    def binary(self, operand, ops: str) -> Node:
        left = operand()
        while self.peek().kind in ops:
            op = self.next()
            right = operand()
            left = self.node(BinOp(left.pos, right.pos + right.length - left.pos, op.kind,
                                   left, right, op.pos))
        return left

    def unary(self) -> Node:
        if self.peek().kind == "-":
            op = self.next()
            self.enter(op)
            arg = self.unary()
            self.depth -= 1
            return self.node(Neg(op.pos, arg.pos + arg.length - op.pos, arg))
        return self.postfix()

    def postfix(self) -> Node:
        p = self.primary()
        if self.peek().kind != "[":
            return p
        open_ = self.next()
        if isinstance(p, Num):
            raise FormulaError("Lags apply to a metric, e.g. revenue[-4q]", open_.pos)
        minus = self.next()
        if minus.kind != "-":
            raise FormulaError("Lags look back: write [-4q] or [-1y]", minus.pos,
                               len(minus.text))
        n = self.next()
        if n.kind != "num" or not n.text.isdigit():
            raise FormulaError("Expected a lag like 4q or 1y", n.pos, len(n.text))
        unit = self.next()
        if unit.kind != "id" or unit.text.lower() not in ("q", "y"):
            raise FormulaError("Lag unit must be q (quarters) or y (years)", unit.pos,
                               len(unit.text))
        quarters = int(n.text) * (4 if unit.text.lower() == "y" else 1)
        if not 1 <= quarters <= MAX_LAG_QUARTERS:
            raise FormulaError(f"Lag must be 1 to {MAX_LAG_QUARTERS} quarters "
                               f"({MAX_LAG_QUARTERS // 4} years)", n.pos,
                               self.end_of(unit) - n.pos, code="limit")
        close = self.next()
        if close.kind != "]":
            raise FormulaError("Missing ']' after the lag", close.pos, len(close.text))
        return self.node(Lag(p.pos, close.pos + 1 - p.pos, p, quarters))

    def primary(self) -> Node:
        tok = self.next()
        if tok.kind == "num":
            return self.node(Num(tok.pos, len(tok.text), Decimal(tok.text)))
        if tok.kind == "(":
            self.enter(tok)
            e = self.expr()
            close = self.next()
            if close.kind != ")":
                raise FormulaError("Missing ')'", close.pos, len(close.text),
                                   "Every '(' needs a matching ')'")
            self.depth -= 1
            return e
        if tok.kind == "id":
            name = tok.text.lower()
            if self.peek().kind == "(":
                if name not in FUNCTIONS:
                    raise FormulaError(f"Unknown function '{tok.text}'", tok.pos,
                                       len(tok.text), "Functions: " + ", ".join(FUNCTIONS),
                                       code="unknown_function")
                self.next()
                self.enter(tok)
                arg = self.expr()
                close = self.next()
                if close.kind != ")":
                    raise FormulaError(f"Missing ')' after {name}(", close.pos,
                                       len(close.text))
                self.depth -= 1
                return self.node(Call(tok.pos, close.pos + 1 - tok.pos, name, arg))
            if self.peek().kind == ".":
                self.next()
                m = self.next()
                if m.kind != "id":
                    raise FormulaError(f"Expected a metric after '{tok.text}.'", m.pos,
                                       len(m.text))
                return self.node(Ref(tok.pos, self.end_of(m) - tok.pos, m.text.lower(),
                                     tok.text.upper(), m.pos, len(tok.text)))
            return self.node(Ref(tok.pos, len(tok.text), name, None, tok.pos))
        if tok.kind == "eof":
            raise FormulaError("Formula ends too early", tok.pos, 1,
                               "Add a metric or number after the operator")
        raise FormulaError(f"Unexpected '{tok.text}'", tok.pos)


def to_dict(n: Node) -> dict:
    """JSON form of the AST."""
    span = {"pos": n.pos, "length": n.length}
    match n:
        case Num(value=v):
            return {"type": "number", "value": str(v), **span}
        case Ref(metric=m, ticker=t):
            return {"type": "ref", "ticker": t, "metric": m, **span}
        case Lag(expr=e, quarters=q):
            return {"type": "lag", "quarters": q, "expr": to_dict(e), **span}
        case Call(name=name, arg=a):
            return {"type": "call", "name": name, "arg": to_dict(a), **span}
        case Neg(arg=a):
            return {"type": "neg", "arg": to_dict(a), **span}
        case BinOp(op=op, left=left, right=right):
            return {"type": "binary", "op": op, "left": to_dict(left), "right": to_dict(right),
                    **span}
    raise TypeError(n)
