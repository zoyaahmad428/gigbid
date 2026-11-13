# RAG assistant over media support documentation

*Media client · 2025-06 · 13 weeks · hourly*

Support agents spent too long searching policy PDFs for answers to repeat questions. I was brought in to fix that.

I started by working out what good looked like with the client, then chunked policy PDFs by heading and indexed them in a vector database. Next I combined BM25 and dense retrieval and reranked the top results. Finally, I answered with citations and refused when no passage supported an answer.

The result: I cut average handling time by 33%, and reached 86% answer accuracy on a 200-question test set.

Tools: Python, RAG, LLM, LangChain, Vector Database.
