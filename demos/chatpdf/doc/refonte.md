## 1. File Text Extraction
When you upload a PDF, the system first:
• extracts the text from the pages  
• makes it searchable  
I do **not** see the whole PDF automatically.

----------

## 2. Semantic Search (Vector Retrieval)
When you ask a question, the system performs:
• semantic search over the extracted text  
• finds the most relevant passages  
• retrieves only those excerpts  

I only receive **those relevant pieces**, not the full file.

----------

## 3. Grounded Answer Generation
I then:
• read the retrieved excerpts  
• answer based on them  
• prioritize the excerpts over general knowledge  
• tell you if no relevant excerpt was found

----------

## 4. General Knowledge (Fallback)
If:
• the system cannot retrieve any relevant text  
or  
• the retrieved text does not contain the answer  

Then I give an answer using general knowledge, clearly labeled as such.

----------

## 5. Conversation Memory (Local Only)
During this conversation:
• I remember the excerpts you already provided  
• I do not store anything outside this session  
• I do not build a long‑term database  

----------

## Summary Table

| Step | Tool / Mechanism | Purpose |
|------|-------------------|---------|
| 1 | Text extraction | Convert PDF pages into searchable text |
| 2 | Semantic search / vector retrieval | Find relevant text for your question |
| 3 | Excerpt-based reasoning | Answer based on retrieved passages |
| 4 | General knowledge fallback | Fill gaps when excerpts lack answers |
| 5 | Local chat memory | Maintain context only within this conversation |

----------

If you want, I can also show you **an example of what a retrieved excerpt looks like**, or explain how semantic search decides relevance.


Here is a **simple, realistic example** of what a retrieved excerpt looks like.  
(This is the *exact style* of text I receive — short, plain text snippets pulled from your PDF.)

---------------------

Example retrieved excerpt:

Page 12, paragraph 3:  
“…Masked language modeling (MLM) is a self‑supervised pre‑training task in which some input tokens are replaced with a mask symbol, and the model is trained to predict the original tokens using bidirectional context…”

Page 13, bullet list:  
“• MLM allows the model to capture both left and right context, unlike autoregressive models.”

Page 4, heading:  
“2.2 Categories of Pre-training Tasks”

---------------------

That's it — very short, plain text segments.  
I do **not** see:

• the page layout  
• images  
• diagrams  
• entire pages  
• your whole PDF  

Only the **specific text that semantic search finds relevant** is sent to me.

If you want, I can show you:
• what multiple excerpts look like  
• what I do if the excerpt is incomplete  
• how I combine excerpts to form an answer