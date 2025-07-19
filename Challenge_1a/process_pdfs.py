from pathlib import Path
import pdfplumber
import json
import re
import os
import unicodedata
import warnings
import logging
import sys
from contextlib import redirect_stderr
from io import StringIO
from jsonschema import validate

# Suppress pdfplumber warnings and logging messages
warnings.filterwarnings("ignore")
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

# Configuration constants to reduce hardcoding
CONFIG = {
    "font_analysis": {
        "max_font_levels": 3,  # Number of font sizes to consider for headings
        "char_proximity_threshold": 2,  # How close characters need to be to group as one text run
        "sample_runs_for_language": 50  # Number of text runs to sample for language detection
    },
    "text_validation": {
        "min_meaningful_chars": 2,
        "min_text_length": 2
    },
    "language_patterns": {
        "japanese": {
            "patterns": [r'第\d+[章節条項部編]', r'[一二三四五六七八九十]\s*[章節条項部編]'],
            "length_range": (1, 25)
        },
        "hindi": {
            "patterns": [r'^(अध्याय|भाग|खंड|विभाग|प्रकरण)', r'^(परिचय|निष्कर्ष|सारांश|विषय)'],
            "length_range": (2, 40)
        },
        "english": {
            "keywords": ['chapter', 'section', 'part', 'introduction', 'conclusion', 
                        'abstract', 'summary', 'overview', 'background', 'references'],
            "length_range": (3, 80)
        }
    },
    "universal_patterns": [
        r'^\d+[\.\)\s]',      # "1.", "1)", "1 "
        r'^\d+\.\d+',         # "1.1", "2.3"
        r'^[IVX]+\.?\s*'      # Roman numerals
    ]
}

# Load the required JSON schema
# Check if running in Docker or local development
if os.environ.get('DOCKER_ENV') or (Path("/app").exists() and not Path(__file__).parent.name == "Challenge_1a"):
    # Docker environment
    schema_path = Path("/app/schema/output_schema.json")
else:
    # Local development environment
    schema_path = Path(__file__).parent / "sample_dataset" / "schema" / "output_schema.json"

with open(schema_path) as f:
    OUTPUT_SCHEMA = json.load(f)

def is_meaningful_text(text, min_length=None):
    """Check if text is meaningful for heading detection across languages."""
    if min_length is None:
        min_length = CONFIG["text_validation"]["min_text_length"]
    
    if not text or len(text.strip()) < min_length:
        return False
    
    # Remove whitespace
    text = text.strip()
    
    # Check if text contains meaningful characters (not just punctuation/numbers)
    meaningful_chars = 0
    for char in text:
        if unicodedata.category(char) in ['Lu', 'Ll', 'Lt', 'Lo', 'Lm']:  # Letter categories
            meaningful_chars += 1
        elif unicodedata.category(char) in ['Nd', 'Nl', 'No']:  # Number categories
            meaningful_chars += 1
    
    # Should have at least minimum meaningful characters
    return meaningful_chars >= CONFIG["text_validation"]["min_meaningful_chars"]

def clean_text(text):
    """Clean and normalize text for different languages."""
    if not text:
        return ""
    
    # Normalize unicode characters
    text = unicodedata.normalize('NFKC', text)
    
    # Remove extra whitespace but preserve structure
    text = re.sub(r'\s+', ' ', text.strip())
    
    return text

def detect_language_script(text):
    """Detect the primary script/language family of the text."""
    if not text:
        return "english"  # Default to english
    
    script_counts = {'english': 0, 'japanese': 0, 'hindi': 0}
    
    for char in text:
        if char.isspace() or unicodedata.category(char).startswith('P'):  # Punctuation
            continue
            
        try:
            char_name = unicodedata.name(char, '')
            
            # Check for specific target languages
            if any(keyword in char_name for keyword in ['CJK', 'HIRAGANA', 'KATAKANA']):
                script_counts['japanese'] += 1
            elif any(keyword in char_name for keyword in ['DEVANAGARI']):
                script_counts['hindi'] += 1
            elif any(keyword in char_name for keyword in ['LATIN']):
                script_counts['english'] += 1
            elif unicodedata.category(char).startswith('L'):  # Any other letter
                script_counts['english'] += 1  # Default to english for unknown letters
        except:
            script_counts['english'] += 1  # Default for any errors
    
    # Return the most common script, with english as fallback
    max_script = max(script_counts, key=script_counts.get)
    return max_script if script_counts[max_script] > 0 else 'english'

def is_likely_heading(text, script_type="english"):
    """Determine if text is likely a heading based on content and script."""
    if not is_meaningful_text(text):
        return False
    
    text = text.strip()
    text_length = len(text)
    
    # Check universal patterns first
    for pattern in CONFIG["universal_patterns"]:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    # Get language-specific configuration
    lang_config = CONFIG["language_patterns"].get(script_type, CONFIG["language_patterns"]["english"])
    min_length, max_length = lang_config["length_range"]
    
    # Check length bounds
    if not (min_length <= text_length <= max_length):
        return False
    
    # Language-specific pattern matching
    if script_type in ["japanese", "hindi"]:
        patterns = lang_config.get("patterns", [])
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return True  # If within length bounds, likely a heading for these languages
        
    else:  # English and default
        # Check for keywords
        keywords = lang_config.get("keywords", [])
        text_lower = text.lower()
        if any(keyword in text_lower for keyword in keywords):
            return True
        
        # Check for ALL CAPS (common heading style)
        if text.isupper() and text_length >= 3:
            return True
        
        # Check for Title Case (common heading style)
        if text.istitle() and text_length >= 5:
            return True
        
        # General check for English
        if text[0].isupper():
            return True
    
    return False

def extract_outline(pdf_path):
    outline = []
    font_stats = {}  # Font size => count of usage
    title_text = ""
    all_text_runs = []  # Store all text runs for language detection
    
    # First pass: collect font statistics and all text
    with redirect_stderr(StringIO()):  # Suppress CropBox warnings
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                chars = page.chars
                for char in chars:
                    # Skip characters that don't have size information
                    if "size" not in char:
                        continue
                    size = round(char["size"], 1)
                    font_stats[size] = font_stats.get(size, 0) + 1

    # Step 1: Identify the top N most common font sizes (bigger => more likely heading)
    max_levels = CONFIG["font_analysis"]["max_font_levels"]
    font_sizes = sorted(font_stats.items(), key=lambda x: -x[0])[:max_levels]
    
    if not font_sizes:
        return {"title": pdf_path.stem, "outline": []}
    
    sizes_to_levels = {font_sizes[0][0]: "H1"}

    if len(font_sizes) > 1:
        sizes_to_levels[font_sizes[1][0]] = "H2"
    if len(font_sizes) > 2:
        sizes_to_levels[font_sizes[2][0]] = "H3"

    # Step 2: Extract text runs and detect language
    with redirect_stderr(StringIO()):  # Suppress CropBox warnings
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Group characters into words by position and font size
                text_runs = []
                current_run = {"text": "", "size": None, "x0": None, "x1": None}
                
                chars = page.chars
                for char in chars:
                    if "size" not in char:
                        continue
                        
                    size = round(char["size"], 1)
                    text = char.get("text", "")
                    x0 = char.get("x0", 0)
                    x1 = char.get("x1", 0)
                    
                    # If this character continues the current run (same size and close position)
                    proximity_threshold = CONFIG["font_analysis"]["char_proximity_threshold"]
                    if (current_run["size"] == size and current_run["x1"] is not None and 
                        abs(x0 - current_run["x1"]) < proximity_threshold):  # Characters very close together
                        current_run["text"] += text
                        current_run["x1"] = x1
                    else:
                        # Save previous run if it has content
                        if current_run["text"].strip():
                            cleaned_text = clean_text(current_run["text"])
                            if cleaned_text:
                                text_runs.append({
                                    "text": cleaned_text,
                                    "size": current_run["size"],
                                    "page": page_num
                                })
                        
                        # Start new run
                        current_run = {
                            "text": text,
                            "size": size,
                            "x0": x0,
                            "x1": x1
                        }
                
                # Don't forget the last run
                if current_run["text"].strip():
                    cleaned_text = clean_text(current_run["text"])
                    if cleaned_text:
                        text_runs.append({
                            "text": cleaned_text,
                            "size": current_run["size"],
                            "page": page_num
                        })
                
                all_text_runs.extend(text_runs)
    
    # Detect the primary language/script of the document
    sample_size = CONFIG["font_analysis"]["sample_runs_for_language"]
    all_text = " ".join([run["text"] for run in all_text_runs[:sample_size]])  # Sample first N runs
    primary_script = detect_language_script(all_text)
    
    # Step 3: Filter and classify headings with multilingual awareness
    for run in all_text_runs:
        if (run["size"] in sizes_to_levels and 
            is_meaningful_text(run["text"]) and
            is_likely_heading(run["text"], primary_script)):
            
            level = sizes_to_levels[run["size"]]
            if not title_text and level == "H1":
                title_text = run["text"]
            outline.append({
                "level": level,
                "text": run["text"],
                "page": run["page"]
            })

    # Step 4: Build JSON with fallback title
    return {
        "title": title_text or pdf_path.stem,
        "outline": outline
    }

def process_pdfs():
    # Check if running in Docker or local development
    if os.environ.get('DOCKER_ENV') or (Path("/app").exists() and not Path(__file__).parent.name == "Challenge_1a"):
        # Docker environment
        input_dir = Path("/app/input")
        output_dir = Path("/app/output")
    else:
        # Local development environment
        base_dir = Path(__file__).parent
        input_dir = base_dir / "sample_dataset" / "pdfs"
        output_dir = base_dir / "sample_dataset" / "outputs"
    
    output_dir.mkdir(parents=True, exist_ok=True)

    for pdf_file in input_dir.glob("*.pdf"):
        print(f"Processing {pdf_file.name}...")
        result = extract_outline(pdf_file)

        # Validate JSON
        try:
            validate(instance=result, schema=OUTPUT_SCHEMA)
        except Exception as e:
            print(f"Schema validation error for {pdf_file.name}: {e}")
            continue

        # Save output
        output_path = output_dir / f"{pdf_file.stem}.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"✓ Done: {output_path.name}")

if __name__ == "__main__":
    process_pdfs()
