# Challenge 1b: Execution Instructions

## Local Execution
```bash
cd Challenge_1b
python process_persona.py
```

## Docker Execution
```bash
cd Challenge_1b
docker build -t challenge-1b .
docker run -v $(pwd)/Collection\ 1:/app/Challenge_1b/Collection\ 1 \
           -v $(pwd)/Collection\ 2:/app/Challenge_1b/Collection\ 2 \
           -v $(pwd)/Collection\ 3:/app/Challenge_1b/Collection\ 3 \
           challenge-1b
```

## Requirements
- Python 3.10+
- Dependencies: pdfplumber, scikit-learn
- CPU-only processing (no GPU required)
- Processing time: <60 seconds per collection

## Output
Generated `challenge1b_output.json` files in each Collection directory with:
- Metadata (documents, persona, job, timestamp)
- Top 5 extracted sections with importance rankings
- Refined text analysis for each section
