#!/usr/bin/env python3
"""
Markdown-to-HTML converter for book notes.

Uses the standard Python `markdown` library with extensions for tables,
fenced code, and smart typography. Handles Obsidian-specific syntax
(wikilinks, inline fields, idézet:: quotes) and wraps the output in a
div per section so it can be embedded in the 3D book page.

Sections recognised (Hungarian headings in the vault):
  ### Idézetek     → <div class="book-section" data-section="quotes">
  ### Vélemény     → <div class="book-section" data-section="opinion">
  ### Említések    → <div class="book-section" data-section="mentions">
  ### Idegen szavak→ <div class="book-section" data-section="foreign-words">
  ### (other)      → <div class="book-section" data-section="other">

Usage:
    from md_to_html import BookMarkdownConverter
    conv = BookMarkdownConverter()
    html = conv.convert(body_markdown)
    # → {"html": "<div class=\"book-content\">...</div>",
    #    "sections": {"quotes": "...", "opinion": "...", ...}}
"""

import re
import html as html_lib
import markdown


# Map Hungarian section headers to English keys
SECTION_MAP = {
    "idézetek": "quotes",
    "vélemény": "opinion",
    "említések": "mentions",
    "idegen szavak": "foreign-words",
    "ami megfogott benne": "highlights",
}

# Hungarian section headers → display labels
SECTION_LABELS = {
    "quotes": "Idézetek",
    "opinion": "Vélemény",
    "mentions": "Említések",
    "foreign-words": "Idegen szavak",
    "highlights": "Amit megfogott",
}


def _preprocess_wikilinks(md_text):
    """Convert Obsidian [[wikilinks]] to HTML spans before markdown processing."""
    def replacer(m):
        target = m.group(1)
        alias = m.group(2) if m.group(2) else target
        return f'<span class="wikilink">{html_lib.escape(alias)}</span>'
    return re.sub(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', replacer, md_text)


def _preprocess_inline_fields(md_text):
    """Convert key:: value lines to styled spans."""
    def replacer(m):
        key = html_lib.escape(m.group(1))
        value = html_lib.escape(m.group(2))
        return f'<span class="inline-field"><b>{key}</b>: {value}</span>'
    return re.sub(r'^(\w+)::\s*(.+)$', replacer, md_text, flags=re.MULTILINE)


def _preprocess_idezet_quotes(md_text):
    """Convert > idézet:: text blockquotes into styled blockquote elements.
    
    Obsidian format:
        > idézet:: Some quote text here.
    
    Becomes:
        <blockquote class="book-quote"><p>Some quote text here.</p></blockquote>
    """
    lines = md_text.split('\n')
    result = []
    in_quote = False
    quote_buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('>'):
            content = re.sub(r'^>\s*', '', line)
            # Remove "idézet::" prefix if present
            content = re.sub(r'^idézet::\s*', '', content)
            content = content.strip()
            if content:
                quote_buffer.append(content)
            in_quote = True
        else:
            if in_quote and quote_buffer:
                quote_html = html_lib.escape(' '.join(quote_buffer))
                # Convert markdown emphasis inside quotes: *text* → <em>text</em>, **text** → <strong>text</strong>
                quote_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', quote_html)
                quote_html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', quote_html)
                result.append(f'\n\n<blockquote class="book-quote"><p>{quote_html}</p></blockquote>\n\n')
                quote_buffer = []
                in_quote = False
            result.append(line)

    if quote_buffer:
        quote_html = html_lib.escape(' '.join(quote_buffer))
        quote_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', quote_html)
        quote_html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', quote_html)
        result.append(f'\n\n<blockquote class="book-quote"><p>{quote_html}</p></blockquote>\n\n')

    return '\n'.join(result)


def _split_sections(md_text):
    """Split markdown body into sections by ### headers.
    
    Returns list of (section_key, section_label, content_md).
    Preamble (before first ###) is returned with key=None.
    """
    parts = re.split(r'^###\s+(.+)$', md_text, flags=re.MULTILINE)
    
    sections = []
    # parts[0] = preamble, then alternating (header, content)
    if parts[0].strip():
        sections.append((None, None, parts[0].strip()))
    
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip()
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        header_lower = header.lower().strip()
        section_key = SECTION_MAP.get(header_lower, header_lower)
        sections.append((section_key, header, content))
    
    return sections


def _markdown_to_clean_html(md_text):
    """Convert markdown to clean HTML using the standard markdown library."""
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "smarty",
            "sane_lists",
        ],
        output_format="html5",
    )
    html = md.convert(md_text)
    # Reset for next use (Markdown is stateful)
    md.reset()
    return html


class BookMarkdownConverter:
    """Convert book note markdown to structured HTML for the 3D page."""
    
    def convert(self, body_md):
        """
        Convert the body markdown of a book note.
        
        Returns a dict:
            {
                "html": "<div class='book-content'>...</div>",  # full HTML
                "sections": {  # per-section HTML
                    "quotes": "<div ...>...</div>",
                    "opinion": "<div ...>...</div>",
                    ...
                }
            }
        """
        sections_md = _split_sections(body_md)
        
        full_html_parts = ['<div class="book-content">']
        section_htmls = {}
        
        for section_key, label, content_md in sections_md:
            if not content_md:
                continue
            
            # Preprocess Obsidian-specific syntax
            content_md = _preprocess_wikilinks(content_md)
            content_md = _preprocess_inline_fields(content_md)
            content_md = _preprocess_idezet_quotes(content_md)
            
            # Convert to HTML
            section_html = _markdown_to_clean_html(content_md)
            
            if section_key is None:
                # Preamble — wrap without a data-section attribute
                full_html_parts.append(
                    f'<div class="book-section book-preamble">{section_html}</div>'
                )
            else:
                label_display = SECTION_LABELS.get(section_key, label)
                wrapped = (
                    f'<div class="book-section" data-section="{html_lib.escape(section_key)}">'
                    f'<h3 class="book-section-title">{html_lib.escape(label_display)}</h3>'
                    f'{section_html}'
                    f'</div>'
                )
                full_html_parts.append(wrapped)
                section_htmls[section_key] = wrapped
        
        full_html_parts.append('</div>')
        
        return {
            "html": '\n'.join(full_html_parts),
            "sections": section_htmls,
        }


def convert_markdown_to_html(body_md):
    """Convenience function: returns just the full HTML string."""
    conv = BookMarkdownConverter()
    return conv.convert(body_md)["html"]


if __name__ == "__main__":
    import sys
    import json
    
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            md = f.read()
        conv = BookMarkdownConverter()
        result = conv.convert(md)
        print(result["html"])
        print("\n--- SECTIONS ---", file=sys.stderr)
        for key, html in result["sections"].items():
            print(f"  {key}: {len(html)} chars", file=sys.stderr)
    else:
        print("Usage: python3 md_to_html.py <file.md>", file=sys.stderr)