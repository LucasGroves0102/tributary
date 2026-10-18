"""Questions each demo user may ask, for the citation-accuracy eval."""

CASES: list[tuple[str, str, str]] = [  # (id, user, question)
    ("fin-01", "alice", "How has Nvidia's revenue changed over the last 4 quarters?"),
    ("fin-02", "alice", "What was Apple's net income last quarter?"),
    ("fin-03", "alice", "Compare Apple's and Nvidia's revenue for the most recent quarter."),
    ("fin-04", "alice", "What was Nvidia's diluted EPS in each of the last 4 quarters?"),
    ("fin-05", "alice", "How much cash did Apple have at the end of the last two quarters?"),
    ("fin-06", "alice", "What is Nvidia's gross margin trend over the last 4 quarters?"),
    ("fin-07", "alice", "Show Apple's operating cash flow for the last 8 quarters."),
    ("fin-08", "alice", "What were Nvidia's total assets at the end of the latest quarter?"),
    ("fin-09", "dave", "What was Microsoft's operating cash flow last quarter?"),
    ("fin-10", "dave", "How did Microsoft's operating income change year over year in the "
                       "latest quarter?"),
    ("fin-11", "dave", "Which had higher net income last quarter, Microsoft or Nvidia, and by "
                       "how much?"),
    ("fin-12", "dave", "Give me Microsoft's revenue, operating income and net income for the "
                       "last quarter."),
    ("fin-13", "bob", "What was Microsoft's revenue growth over the last 4 quarters?"),
    ("fin-14", "bob", "How much did Nvidia's revenue grow between its two most recent quarters?"),
    ("fin-15", "bob", "What's Nvidia's operating margin last quarter?"),
    ("fin-16", "alice", "What was Apple's revenue in the quarter before last?"),
    ("fin-17", "dave", "Summarize Nvidia's last quarter: revenue, net income and EPS."),
    ("fin-18", "alice", "How has Apple's revenue trended over the last year?"),
    ("doc-01", "bob", "Summarize the top risk factors in Nvidia's latest 10-K."),
    ("doc-02", "bob", "What does Microsoft's latest 10-Q say in its MD&A about cloud revenue?"),
    ("doc-03", "bob", "What did Nvidia's most recent 8-K report?"),
    ("doc-04", "bob", "What legal proceedings does Microsoft's latest 10-K describe?"),
    ("doc-05", "bob", "What does Nvidia's latest 10-Q say about liquidity and capital resources?"),
    ("doc-06", "bob", "List the filings Microsoft has made recently."),
    ("doc-07", "bob", "What does Nvidia's latest 10-K say about competition?"),
    ("mix-01", "bob", "What was Nvidia's revenue last quarter, and what does management say "
                      "drove it?"),
    ("mix-02", "bob", "Compare Microsoft's and Nvidia's net income last quarter and summarize "
                      "each one's main risk."),
    ("mix-03", "bob", "Did Microsoft's operating income grow last quarter? Cite the filing."),
    ("den-01", "alice", "What was Tesla's revenue last quarter?"),
    ("den-02", "carol", "What was Nvidia's revenue last quarter?"),
]
