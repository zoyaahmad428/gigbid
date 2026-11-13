# ops-agent

Tool-using LLM agent for e-commerce operations staff.

## What it does
Staff had to look up the same data across three internal systems. This is the code from when I built the agent as a graph with a human approval step before any write.

## Stack
agent graph, tool definitions, approval step; Python, LLM, LangGraph, LangChain.

## Run
```bash
make install
make run
```

Client data is not included. Results in production: answered 65% of lookup requests without a person.
