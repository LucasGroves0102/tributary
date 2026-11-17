"""What POST /v1/sql may run (B3a): ONE SELECT over the `workspace` schema, checked on pglast's
parse tree (pglast 6, libpg_query 16: Postgres 16's grammar, hermes' major version).

Default deny, node by node: every node of the tree must be of a type in NODES; then
  * the statement is one SelectStmt: no INTO, no FOR UPDATE/SHARE, no WITH RECURSIVE, every CTE a
    SELECT (never INSERT/UPDATE/DELETE/MERGE); no $1 parameters;
  * every relation is a `workspace` view (RELATIONS), unqualified or `workspace.`-qualified, or a
    CTE in scope where it is named (a CTE named pg_class outside its WITH is pg_class: refused);
  * every function is in FUNCTIONS (pg_catalog built-ins without side effects: maths, aggregates,
    window functions, dates, text, arrays) or WORKSPACE_FUNCTIONS; never set_config,
    current_setting, pg_sleep, generate_series, advisory locks, large objects, dblink, *_to_xml...;
  * every cast is to a type in TYPES (no regclass/regproc: no catalog lookups);
  * operators are unqualified (search_path is `workspace`, so only pg_catalog's resolve);
  * CURRENT_DATE and the other clock values are the only SQL value functions (no CURRENT_USER).
NUL bytes are refused before parsing (libpg_query would stop at one).

What runs is not the user's text: the checked tree as pglast deparses it (no comments, canonical
quoting), parsed and checked again, so the statement Postgres plans is one this module passed.
`table.func` notation (`c.to_json` = to_json(c)) reaches only one-argument functions of a row:
pg_catalog has none with a side effect (num_nulls, to_json, row_to_json...).

The database is the second line: workspace_reader (migration 105) has SELECT on the views and the
grants their bodies need, no SECURITY DEFINER function, and row-level security as query_api has;
the transaction is READ ONLY. Nothing here decides who reads what: RLS does."""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pglast import ast, enums, parse_sql
from pglast.parser import ParseError
from pglast.stream import RawStream

MAX_SQL_CHARS = 20_000

# migration 105's views and functions (the schema browser lists them from the catalog)
RELATIONS = frozenset({
    "companies", "metrics", "financials", "financials_history", "latest_figures", "filings",
    "sections", "my_datasets", "my_series"})
WORKSPACE_FUNCTIONS = frozenset({"financials_at", "restatements", "reading"})

# pg_catalog functions a query may call: pure (no side effects, no catalog or session reads, no
# sleeping, locking or I/O) and bounded by the data (no repeat/lpad/generate_series to build
# large values or rows from nothing). No array_agg or string_agg: an aggregate's value isn't bounded
# by work_mem, so one over a whole view is a single value of hundreds of MB in Postgres and again in
# the Query API.
FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "any_value", "stddev", "stddev_pop", "stddev_samp",
    "variance", "var_pop", "var_samp", "corr", "covar_pop", "covar_samp", "regr_avgx",
    "regr_avgy", "regr_count", "regr_intercept", "regr_r2", "regr_slope", "regr_sxx", "regr_sxy",
    "regr_syy", "bool_and", "bool_or", "every", "percentile_cont", "percentile_disc", "mode",
    # window functions
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value",
    # maths
    "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign", "sqrt", "cbrt", "exp", "ln",
    "log", "log10", "power", "pow", "mod", "div", "pi", "degrees", "radians", "width_bucket",
    "scale", "min_scale", "trim_scale", "gcd", "lcm", "num_nulls", "num_nonnulls",
    # dates and times
    "date_trunc", "date_part", "extract", "age", "make_date", "make_interval", "make_timestamp",
    "date_bin", "to_char", "to_date", "to_timestamp", "to_number", "isfinite", "justify_days",
    "justify_hours", "justify_interval", "now", "timezone", "overlaps",
    # text
    "lower", "upper", "initcap", "length", "char_length", "character_length", "octet_length",
    "substr", "substring", "left", "right", "btrim", "ltrim", "rtrim", "replace", "split_part",
    "concat", "concat_ws", "position", "strpos", "starts_with", "reverse", "translate", "format",
    "ascii", "chr", "overlay", "normalize", "is_normalized", "like_escape", "similar_to_escape",
    "regexp_replace", "regexp_match", "regexp_matches", "regexp_like", "regexp_count",
    "regexp_substr", "regexp_instr", "regexp_split_to_array", "string_to_array",
    "array_to_string",
    # arrays
    "array_length", "cardinality", "array_position", "array_positions", "array_append",
    "array_prepend", "array_cat", "array_remove", "array_replace", "array_lower", "array_upper",
    "array_ndims", "trim_array", "unnest",
})

# cast targets: as the parser spells them, unqualified or pg_catalog's internal names
TYPES = frozenset({
    "int", "integer", "smallint", "bigint", "int2", "int4", "int8", "real", "float", "float4",
    "float8", "numeric", "decimal", "text", "varchar", "char", "bpchar", "bool", "boolean",
    "date", "time", "timetz", "timestamp", "timestamptz", "interval"})

# node types a query may contain; anything else (ParamRef, LockingClause, IntoClause,
# RangeTableSample, XMLTABLE, JSON constructors, COLLATE, MultiAssignRef...) is refused
NODES = frozenset({
    "SelectStmt", "ResTarget", "ColumnRef", "A_Star", "A_Const", "String", "Integer", "Float",
    "Boolean", "A_Expr", "BoolExpr", "NullTest", "BooleanTest", "CaseExpr", "CaseWhen",
    "CoalesceExpr", "MinMaxExpr", "SubLink", "RowExpr", "A_ArrayExpr", "A_Indices",
    "A_Indirection", "TypeCast", "TypeName", "FuncCall", "WindowDef", "SortBy", "GroupingSet",
    "GroupingFunc", "RangeVar", "RangeSubselect", "RangeFunction", "JoinExpr", "Alias",
    "CommonTableExpr", "WithClause", "SQLValueFunction"})

_OPS = enums.A_Expr_Kind
OPERATOR_KINDS = frozenset({
    _OPS.AEXPR_OP, _OPS.AEXPR_OP_ANY, _OPS.AEXPR_OP_ALL, _OPS.AEXPR_DISTINCT,
    _OPS.AEXPR_NOT_DISTINCT, _OPS.AEXPR_NULLIF, _OPS.AEXPR_IN, _OPS.AEXPR_LIKE, _OPS.AEXPR_ILIKE,
    _OPS.AEXPR_SIMILAR, _OPS.AEXPR_BETWEEN, _OPS.AEXPR_NOT_BETWEEN, _OPS.AEXPR_BETWEEN_SYM,
    _OPS.AEXPR_NOT_BETWEEN_SYM})
_SVF = enums.SQLValueFunctionOp
CLOCKS = frozenset({
    _SVF.SVFOP_CURRENT_DATE, _SVF.SVFOP_CURRENT_TIME, _SVF.SVFOP_CURRENT_TIME_N,
    _SVF.SVFOP_CURRENT_TIMESTAMP, _SVF.SVFOP_CURRENT_TIMESTAMP_N, _SVF.SVFOP_LOCALTIME,
    _SVF.SVFOP_LOCALTIME_N, _SVF.SVFOP_LOCALTIMESTAMP, _SVF.SVFOP_LOCALTIMESTAMP_N})

# what a refusal calls a node (never the user's text beyond a name they typed)
_WORDS = {"ParamRef": "parameters ($1)", "LockingClause": "FOR UPDATE / FOR SHARE",
          "IntoClause": "SELECT INTO", "CollateClause": "COLLATE",
          "RangeTableSample": "TABLESAMPLE", "RangeTableFunc": "XMLTABLE",
          "MultiAssignRef": "multiple assignment", "SetToDefault": "DEFAULT"}


class Refused(ValueError):
    """The query can't run here: `message` says why (names only, never data); `position` is a
    0-based character offset into the submitted text when the parser gave one."""

    def __init__(self, message: str, position: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.position = position


@dataclass(frozen=True)
class Checked:
    sql: str                     # what runs: the checked statement, deparsed
    relations: frozenset[str]    # the workspace views it reads
    functions: frozenset[str]    # the workspace functions it calls


def check(text: str) -> Checked:
    """`text` as one checked SELECT, or Refused."""
    if not text.strip():
        raise Refused("the query is empty")
    if len(text) > MAX_SQL_CHARS:
        raise Refused(f"the query is longer than {MAX_SQL_CHARS:,} characters")
    if "\x00" in text:
        raise Refused("the query contains a NUL character")
    walker = _check(text, user_text=True)
    try:
        deparsed = RawStream()(walker.statement)
    except Exception:  # noqa: BLE001 - pglast raises several kinds on what it can't print
        raise Refused("this query can't be run here") from None
    again = _check(deparsed, user_text=False)  # what Postgres will parse
    return Checked(deparsed, frozenset(again.relations), frozenset(again.functions))


def _check(text: str, *, user_text: bool) -> _Walker:
    try:
        statements = parse_sql(text)
    except ParseError as e:
        if not user_text:
            raise Refused("this query can't be run here") from None
        message, position = (e.args + (None, None))[:2]
        raise Refused(f"{message}" if str(message).startswith("syntax") else
                      f"syntax error: {message}",
                      position if isinstance(position, int) else None) from None
    except RecursionError:
        raise Refused("the query is nested too deeply") from None
    if len(statements) != 1:
        raise Refused("one statement at a time" if statements else "the query is empty")
    stmt = statements[0].stmt
    if not isinstance(stmt, ast.SelectStmt):
        raise Refused(f"only SELECT queries run here, not {_kind(stmt)}")
    walker = _Walker(statements[0])
    try:
        walker.select(stmt, ())
    except RecursionError:
        raise Refused("the query is nested too deeply") from None
    return walker


def _kind(node: Any) -> str:
    name = type(node).__name__
    return name[:-4].upper() if name.endswith("Stmt") else name


def _names(items: Iterable[Any]) -> list[str]:
    out = []
    for i in items or ():
        if not isinstance(i, ast.String):
            raise Refused("this name can't be used here")
        out.append(i.sval)
    return out


class _Walker:
    def __init__(self, raw: ast.RawStmt) -> None:
        self.statement = raw
        self.relations: set[str] = set()
        self.functions: set[str] = set()

    # --- statements and scopes ----------------------------------------------------------

    def select(self, s: ast.SelectStmt, scope: tuple[str, ...]) -> None:
        if s.intoClause is not None:
            raise Refused("SELECT INTO can't run here: nothing is written")
        if s.lockingClause:
            raise Refused("FOR UPDATE / FOR SHARE can't run here: queries are read only")
        if s.withClause is not None:
            scope = self.with_clause(s.withClause, scope)
        for slot in s.__slots__:
            if slot in ("withClause", "intoClause", "lockingClause"):
                continue
            self.visit(getattr(s, slot), scope)

    def with_clause(self, w: ast.WithClause, scope: tuple[str, ...]) -> tuple[str, ...]:
        if w.recursive:
            raise Refused("WITH RECURSIVE can't run here")
        for cte in w.ctes or ():
            if not isinstance(cte, ast.CommonTableExpr):
                raise Refused("this WITH can't run here")
            if not isinstance(cte.ctequery, ast.SelectStmt):
                raise Refused(f"WITH {cte.ctename} must be a SELECT: queries are read only")
            if cte.search_clause is not None or cte.cycle_clause is not None:
                raise Refused("SEARCH / CYCLE can't run here")
            # a CTE sees the ones before it in the same WITH (not itself: not recursive)
            self.select(cte.ctequery, scope)
            _names(cte.aliascolnames)
            scope = (*scope, cte.ctename)
        return scope

    # --- the generic walk: every node type must be allowed --------------------------------

    def visit(self, node: Any, scope: tuple[str, ...]) -> None:
        if node is None or isinstance(node, (bool, int, float, str, enum.Enum)):
            return
        if isinstance(node, (tuple, list)):
            for n in node:
                self.visit(n, scope)
            return
        name = type(node).__name__
        if name not in NODES:
            raise Refused(f"{_WORDS.get(name, name)} can't be used here")
        handler = getattr(self, f"_{name}", None)
        if handler is not None:
            handler(node, scope)
            return
        for slot in node.__slots__:
            if slot != "location":
                self.visit(getattr(node, slot), scope)

    def _SelectStmt(self, node: ast.SelectStmt, scope: tuple[str, ...]) -> None:  # noqa: N802
        self.select(node, scope)

    def _WithClause(self, node: ast.WithClause, scope: tuple[str, ...]) -> None:  # noqa: N802
        raise Refused("WITH is only allowed at the start of a SELECT")  # handled by select()

    def _CommonTableExpr(self, node, scope) -> None:  # noqa: N802
        raise Refused("WITH is only allowed at the start of a SELECT")

    def _RangeVar(self, node: ast.RangeVar, scope: tuple[str, ...]) -> None:  # noqa: N802
        if node.catalogname is not None:
            raise Refused(f"{node.catalogname}.… can't be read here")
        if node.schemaname is None and node.relname in scope:
            pass  # a CTE in scope (Postgres prefers it to any table of that name)
        elif node.schemaname in (None, "workspace") and node.relname in RELATIONS:
            self.relations.add(node.relname)
        else:
            shown = f"{node.schemaname}.{node.relname}" if node.schemaname else node.relname
            raise Refused(f"{shown} isn't in the workspace: read workspace views only"
                          " (the schema browser lists them)")
        self.visit(node.alias, scope)

    def _RangeFunction(self, node: ast.RangeFunction, scope) -> None:  # noqa: N802
        if node.coldeflist:
            raise Refused("a column definition list can't be used here")
        for item in node.functions or ():
            call, coldefs = (tuple(item) + (None, None))[:2]
            if coldefs:
                raise Refused("a column definition list can't be used here")
            if not isinstance(call, ast.FuncCall):
                raise Refused("only functions can be read in FROM")
            self.visit(call, scope)
        self.visit(node.alias, scope)

    def _FuncCall(self, node: ast.FuncCall, scope: tuple[str, ...]) -> None:  # noqa: N802
        names = _names(node.funcname)
        if len(names) == 1 and names[0] in FUNCTIONS | WORKSPACE_FUNCTIONS:
            name = names[0]
        elif len(names) == 2 and names[0] == "pg_catalog" and names[1] in FUNCTIONS:
            name = names[1]
        elif len(names) == 2 and names[0] == "workspace" and names[1] in WORKSPACE_FUNCTIONS:
            name = names[1]
        else:
            raise Refused(f"the function {'.'.join(names)} can't be used here")
        if name in WORKSPACE_FUNCTIONS:
            self.functions.add(name)
        for slot in ("args", "agg_order", "agg_filter", "over"):
            self.visit(getattr(node, slot), scope)

    def _TypeName(self, node: ast.TypeName, scope: tuple[str, ...]) -> None:  # noqa: N802
        names = _names(node.names)
        if node.setof or node.pct_type:
            raise Refused("this type can't be used here")
        if not ((len(names) == 1 and names[0] in TYPES)
                or (len(names) == 2 and names[0] == "pg_catalog" and names[1] in TYPES)):
            raise Refused(f"casts to {'.'.join(names)} aren't allowed: numbers, text, booleans,"
                          " dates, times and intervals only")
        self.visit(node.typmods, scope)
        self.visit(node.arrayBounds, scope)

    def _A_Expr(self, node: ast.A_Expr, scope: tuple[str, ...]) -> None:  # noqa: N802
        if node.kind not in OPERATOR_KINDS:
            raise Refused("this operator can't be used here")
        if len(_names(node.name)) != 1:
            raise Refused("qualified operators (OPERATOR(schema.op)) can't be used here")
        self.visit(node.lexpr, scope)
        self.visit(node.rexpr, scope)

    def _SubLink(self, node: ast.SubLink, scope: tuple[str, ...]) -> None:  # noqa: N802
        if node.operName and len(_names(node.operName)) != 1:
            raise Refused("qualified operators (OPERATOR(schema.op)) can't be used here")
        if not isinstance(node.subselect, ast.SelectStmt):
            raise Refused("a subquery must be a SELECT")
        self.visit(node.testexpr, scope)
        self.select(node.subselect, scope)

    def _SortBy(self, node: ast.SortBy, scope: tuple[str, ...]) -> None:  # noqa: N802
        if node.useOp and len(_names(node.useOp)) != 1:
            raise Refused("qualified operators (OPERATOR(schema.op)) can't be used here")
        self.visit(node.node, scope)

    def _SQLValueFunction(self, node: ast.SQLValueFunction, scope) -> None:  # noqa: N802
        if node.op not in CLOCKS:
            raise Refused("only CURRENT_DATE, CURRENT_TIMESTAMP and the other clock values can be"
                          " used here")

    def _A_Const(self, node: ast.A_Const, scope: tuple[str, ...]) -> None:  # noqa: N802
        if node.val is not None and not isinstance(
                node.val, (ast.Integer, ast.Float, ast.String, ast.Boolean)):
            raise Refused("this kind of constant can't be used here")

    def _ColumnRef(self, node: ast.ColumnRef, scope: tuple[str, ...]) -> None:  # noqa: N802
        for f in node.fields or ():
            if not isinstance(f, (ast.String, ast.A_Star)):
                raise Refused("this column reference can't be used here")
