## What I Built
A Python command-line RAG tool that answers questions from a folder of documents. It splits each document into chunks and stores them in a Chroma database. The tool will retrieves the 4 most relevant chunks for each question, and sends them to Claude (claude-sonnet-5) with instructions to answer only from those sources. Claude will cite the source file for each fact, and say so when the sources don't contain the answer. To use it with Larkfield's documents, I added a --docs option to choose the folder and a --save option to save each run 

## Why This Approach
Chunking method: structure
Makes sense on its own: Yes, because each chunk is one whole section under its heading so lists and rules stay together
Label and source: Both, because every chunk starts with its document's title and section heading
Short enough: Yes, because ask.py splits any section longer than 170 words. Longest chunk was 136 words for Copperline and 117 words for Larkfield.

## Example Run
```
Rank 1 | Similarity 0.627 | consumer-fee-schedule.html
    Consumer Fee Schedule (Version 8) Wire transfers - Incoming domestic wire transfer: $10. - Incoming international wire transfer: $15. - Outgoing domes

Rank 2 | Similarity 0.598 | fee-refund-guide.html
    Fee Refund Guide for Member Service Staff (Version 2) Refunds that need a branch manager A refund needs the branch manager's approval when the fee is 

Rank 3 | Similarity 0.579 | fee-refund-guide.html
    Fee Refund Guide for Member Service Staff (Version 2) What a member service representative can approve A member service representative may approve a r

Rank 4 | Similarity 0.492 | fee-refund-guide.html
    Fee Refund Guide for Member Service Staff (Version 2) Refunds that need the Member Services Director Refunds of more than $150 in total for one member

ANSWER
No. The outgoing international wire transfer fee is $45 [consumer-fee-schedule.html]. A member service representative can only approve a refund of a single fee of $30 or less without anyone else's approval [fee-refund-guide.html]. Since $45 exceeds $30, it requires branch manager approval [fee-refund-guide.html].
```

## Reflection
What was wrong: The outdated consumer-fee-schedule-v7.html was in the larkfield-docs folder next to the current consumer-fee-schedule.html (v8). The retrieved chunks for Question 1 showed both versions' overdraft sections with almost the same similarity (0.726 and 0.719), so the answer gave both the $29 and the $35 fee.
How I fixed it: Moved consumer-fee-schedule-v7.html out of larkfield-docs into a separate larkfield-docs-archive folder, then rebuilt the Larkfield database.
Prevention: Archive process. Whenever Larkfield publishes a new version of a document, its owner moves the old version to an archive folder outside the searched folder and rebuilds the database the same day.
Question 6: Declined, because retrieval returned only unrelated chunks with very low similarity scores between 0.175 and 0.246.
