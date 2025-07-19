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

def calculate_heading_confidence(text, font_size=None, position_info=None, context=None):
    """Calculate confidence score for text being a heading using multiple factors."""
    confidence = 0.0
    text_clean = text.strip()
    
    if not text_clean:
        return 0.0
    
    # Factor 1: Pattern-based scoring (40% weight)
    pattern_score = 0.0
    
    # Universal numbering patterns
    for pattern in CONFIG["universal_patterns"]:
        if re.search(pattern, text_clean, re.IGNORECASE):
            pattern_score = 1.0
            break
    
    # Language-specific patterns
    script_type = detect_language_script(text_clean)
    lang_config = CONFIG["language_patterns"].get(script_type, CONFIG["language_patterns"]["english"])
    
    if script_type in ["japanese", "hindi"]:
        patterns = lang_config.get("patterns", [])
        for pattern in patterns:
            if re.search(pattern, text_clean):
                pattern_score = max(pattern_score, 0.9)
                break
    else:  # English
        keywords = lang_config.get("keywords", [])
        text_lower = text_clean.lower()
        if any(keyword in text_lower for keyword in keywords):
            pattern_score = max(pattern_score, 0.8)
    
    confidence += pattern_score * 0.4
    
    # Factor 2: Text formatting (30% weight)
    format_score = 0.0
    
    # ALL CAPS
    if text_clean.isupper() and len(text_clean) >= 3:
        format_score = max(format_score, 0.8)
    
    # Title Case
    elif text_clean.istitle() and len(text_clean) >= 5:
        format_score = max(format_score, 0.7)
    
    # Starts with capital
    elif text_clean[0].isupper():
        format_score = max(format_score, 0.4)
    
    confidence += format_score * 0.3
    
    # Factor 3: Length characteristics (20% weight)
    length_score = 0.0
    text_length = len(text_clean)
    min_length, max_length = lang_config["length_range"]
    
    if min_length <= text_length <= max_length:
        # Optimal length range gets full score
        length_score = 1.0
    elif text_length < min_length:
        # Too short - penalize heavily
        length_score = 0.2
    elif text_length > max_length:
        # Too long - moderate penalty
        length_score = max(0.3, 1.0 - (text_length - max_length) / max_length)
    
    confidence += length_score * 0.2
    
    # Factor 4: Font size relative importance (10% weight)
    font_score = 0.0
    if font_size and context and "font_percentiles" in context:
        percentiles = context["font_percentiles"]
        if font_size >= percentiles.get("90th", 0):
            font_score = 1.0  # Top 10% of font sizes
        elif font_size >= percentiles.get("75th", 0):
            font_score = 0.7  # Top 25% of font sizes
        elif font_size >= percentiles.get("50th", 0):
            font_score = 0.4  # Above median
        else:
            font_score = 0.1  # Below median - less likely heading
    
    confidence += font_score * 0.1
    
    return min(confidence, 1.0)  # Cap at 1.0

def determine_heading_level(confidence, text, context=None):
    """Determine heading level based on confidence and additional context."""
    
    # High confidence patterns get priority
    if confidence >= 0.8:
        # Check for chapter/major section patterns
        if re.search(r'(chapter|chapter\s+\d+|第\d+章|अध्याय)', text.lower()):
            return "H1"
        elif re.search(r'(\d+\.\d+|\d+\.\d+\.\d+|section)', text.lower()):
            return "H2"
        else:
            return "H1"  # Default high confidence to H1
    
    elif confidence >= 0.6:
        # Medium confidence - usually H2 or H3
        if re.search(r'(\d+\.\d+\.\d+)', text):
            return "H3"
        elif re.search(r'(\d+\.\d+)', text):
            return "H2"
        else:
            return "H2"  # Default medium confidence to H2
    
    elif confidence >= 0.4:
        # Lower confidence - likely H3
        return "H3"
    
    else:
        # Below threshold - not a heading
        return None

def extract_outline(pdf_path):
    outline = []
    font_stats = {}  # Font size => count of usage
    title_text = ""
    all_text_runs = []  # Store all text runs for language detection
    
    # First pass: collect font statistics for context
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
    
    # Calculate font size percentiles for context
    all_sizes = []
    for size, count in font_stats.items():
        all_sizes.extend([size] * count)
    
    font_percentiles = {}
    if all_sizes:
        all_sizes.sort()
        font_percentiles = {
            "50th": all_sizes[len(all_sizes) // 2] if all_sizes else 0,
            "75th": all_sizes[int(len(all_sizes) * 0.75)] if all_sizes else 0,
            "90th": all_sizes[int(len(all_sizes) * 0.90)] if all_sizes else 0,
        }
    
    context = {"font_percentiles": font_percentiles}
    
    # Step 3: Analyze each text run for heading likelihood using confidence scoring
    heading_candidates = []
    for run in all_text_runs:
        if not is_meaningful_text(run["text"]):
            continue
            
        confidence = calculate_heading_confidence(
            text=run["text"],
            font_size=run["size"],
            context=context
        )
        
        # Use confidence threshold instead of binary classification
        if confidence >= 0.4:  # Adjustable threshold
            level = determine_heading_level(confidence, run["text"], context)
            if level:
                heading_candidates.append({
                    "text": run["text"],
                    "level": level,
                    "page": run["page"],
                    "confidence": confidence,
                    "size": run["size"]
                })
    
    # Step 4: Post-process and finalize headings
    # Sort by confidence and remove duplicates/overlaps
    heading_candidates.sort(key=lambda x: -x["confidence"])
    
    seen_texts = set()
    for candidate in heading_candidates:
        # Skip near-duplicates
        text_normalized = re.sub(r'\s+', ' ', candidate["text"].lower().strip())
        if text_normalized in seen_texts:
            continue
        seen_texts.add(text_normalized)
        
        # Set title from first high-confidence H1
        if not title_text and candidate["level"] == "H1" and candidate["confidence"] >= 0.7:
            title_text = candidate["text"]
        
        outline.append({
            "level": candidate["level"],
            "text": candidate["text"],
            "page": candidate["page"]
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
