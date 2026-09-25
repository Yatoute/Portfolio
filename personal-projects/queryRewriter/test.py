import arxiv

# Construct the default API client.
client = arxiv.Client()

# Search for the 10 most recent articles matching the keyword "quantum."
search = arxiv.Search(
  query = "cat:stat.OT",
  max_results = 100,
  sort_by=arxiv.SortCriterion.SubmittedDate,
)


doc_count = 0
for result in client.results(search):

  doc_count += 1
  print(result.title)
  print(result.updated, "\n\n")
  
  
print(doc_count)