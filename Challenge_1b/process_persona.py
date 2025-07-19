

from pathlib import Path
import pdfplumber
import json
import os
import re
import unicodedata
from datetime import datetime

# Try to import sklearn, install if missing
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    print("Installing required scikit-learn...")
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scikit-learn"])
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

# Dynamic path detection for local vs Docker environment
import sys

print(f"Environment check:")
print(f"  DOCKER_ENV: {os.environ.get('DOCKER_ENV')}")
print(f"  /app/Challenge_1b exists: {Path('/app/Challenge_1b').exists()}")

# Only use Docker path if we're actually in Docker environment
if os.environ.get('DOCKER_ENV') and Path("/app/Challenge_1b").exists():
    BASE_DIR = Path("/app/Challenge_1b")
    print("Using Docker path")
else:
    # Use current working directory
    BASE_DIR = Path.cwd()
    print("Using local path")
    
print(f"Current working directory: {Path.cwd()}")
print(f"Using BASE_DIR: {BASE_DIR}")
print(f"BASE_DIR exists: {BASE_DIR.exists()}")

def extract_outline_and_paragraphs(pdf_path):
    print(f"  Extracting from: {pdf_path}")
    sections = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if not text:
                    continue
                lines = text.split("\n")
                for line in lines:
                    line_clean = line.strip()
                    if len(line_clean) >= 5 and line_clean[0].isupper():
                        sections.append({
                            "document": pdf_path.name,
                            "page_number": page_num,
                            "section_title": line_clean,
                            "text": line_clean
                        })
        print(f"  Found {len(sections)} sections")
        return sections
    except Exception as e:
        print(f"  Error extracting from {pdf_path}: {e}")
        return []

# Step 2: Rank relevance using TF-IDF
def rank_sections(sections, persona, job):
    if not sections:
        print("  No sections to rank")
        return []
    
    print(f"  Ranking {len(sections)} sections for persona: '{persona}', job: '{job}'")
    try:
        query = f"{persona}. {job}"
        corpus = [query] + [s["text"] for s in sections]
        tfidf = TfidfVectorizer().fit_transform(corpus)
        scores = cosine_similarity(tfidf[0:1], tfidf[1:]).flatten()
        for idx, score in enumerate(scores):
            sections[idx]["score"] = float(score)
        ranked = sorted(sections, key=lambda x: -x["score"])
        print(f"  Top score: {ranked[0]['score']:.4f}" if ranked else "  No ranked results")
        return ranked
    except Exception as e:
        print(f"  Error in ranking: {e}")
        return sections

# Step 3: Build output JSON
def build_output_json(sections, input_data):
    output = {
        "metadata": {
            "input_documents": [d["filename"] for d in input_data.get("documents", [])],
            "persona": input_data.get("persona", {}).get("role", ""),
            "job_to_be_done": input_data.get("job_to_be_done", {}).get("task", ""),
            "processing_timestamp": datetime.now().isoformat()
        },
        "extracted_sections": [],
        "subsection_analysis": []
    }

    for rank, sec in enumerate(sections[:5], start=1):
        output["extracted_sections"].append({
            "document": sec["document"],
            "page_number": sec["page_number"],
            "section_title": sec["section_title"],
            "importance_rank": rank
        })
        output["subsection_analysis"].append({
            "document": sec["document"],
            "page_number": sec["page_number"],
            "refined_text": sec["text"]
        })
    return output

# Step 4: Process each collection folder
def process_collections():
    print(f"Looking for collections in: {BASE_DIR}")
    
    if not BASE_DIR.exists():
        print(f"ERROR: BASE_DIR does not exist: {BASE_DIR}")
        return
    
    collection_dirs = list(BASE_DIR.glob("Collection */"))
    print(f"Found {len(collection_dirs)} collection directories")
    
    if not collection_dirs:
        print("No collection directories found. Looking for any directories...")
        all_dirs = [d for d in BASE_DIR.iterdir() if d.is_dir()]
        print(f"All directories: {[d.name for d in all_dirs]}")
        return
    
    for collection_dir in collection_dirs:
        print(f"\n--- Processing collection: {collection_dir.name} ---")
        
        input_path = collection_dir / "challenge1b_input.json"
        pdf_dir = collection_dir / "PDFs"
        output_path = collection_dir / "challenge1b_output.json"

        print(f"Input file: {input_path} (exists: {input_path.exists()})")
        print(f"PDF directory: {pdf_dir} (exists: {pdf_dir.exists()})")

        if not input_path.exists() or not pdf_dir.exists():
            print(f"Skipping {collection_dir} — input or PDFs folder missing.")
            continue

        try:
            with open(input_path, "r") as f:
                input_data = json.load(f)
            print(f"Loaded input data successfully")
        except Exception as e:
            print(f"Error loading input file: {e}")
            continue

        persona = input_data.get("persona", {}).get("role", "")
        job = input_data.get("job_to_be_done", {}).get("task", "")
        print(f"Persona: '{persona}', Job: '{job}'")

        all_sections = []
        for doc in input_data.get("documents", []):
            pdf_file = pdf_dir / doc["filename"]
            if not pdf_file.exists():
                print(f"Warning: {pdf_file.name} not found. Skipping.")
                continue
            sections = extract_outline_and_paragraphs(pdf_file)
            all_sections.extend(sections)

        print(f"Total sections extracted: {len(all_sections)}")

        if not all_sections:
            print("No sections found, creating minimal output")
            result = {
                "metadata": {
                    "input_documents": [d["filename"] for d in input_data.get("documents", [])],
                    "persona": persona,
                    "job_to_be_done": job
                },
                "extracted_sections": [],
                "subsection_analysis": []
            }
        else:
            ranked = rank_sections(all_sections, persona, job)
            result = build_output_json(ranked, input_data)

        try:
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"✓ Output written to: {output_path}")
        except Exception as e:
            print(f"Error writing output: {e}")

if __name__ == "__main__":
    print("Starting process_persona.py...")
    try:
        process_collections()
        print("Processing completed!")
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
