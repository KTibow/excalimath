#!/usr/bin/env python3
"""
Add SVG glyphs to a font file, converting from SVG paths to TrueType outlines.
"""

import os
import re
from fontTools.ttLib import TTFont, newTable
from fontTools.svgLib import SVGPath
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.misc.transform import Transform

def clean_svg_content(svg_content):
    """Remove XML/encoding declarations that cause parsing issues"""
    # Remove XML declaration
    svg_content = re.sub(r'<\?xml[^>]+\?>', '', svg_content)
    # Remove encoding attributes
    svg_content = re.sub(r'encoding="[^"]+"', '', svg_content)
    return svg_content

def get_svg_viewbox(svg_content):
    """Extract viewBox from SVG to help with scaling"""
    viewbox_match = re.search(r'viewBox=["\']([^"\']+)["\']', svg_content)
    if viewbox_match:
        try:
            viewbox = [float(n) for n in viewbox_match.group(1).split()]
            return viewbox
        except (ValueError, IndexError):
            pass
    return None

def calculate_transform(svg_content, units_per_em, advance_width, special_x):
    """Calculate transformation matrix to scale SVG to font units with proper alignment"""
    viewbox = get_svg_viewbox(svg_content)

    if viewbox:
        # Extract viewBox dimensions
        x, y, width, height = viewbox

        # Calculate scaling factors to fit in the em square while preserving aspect ratio
        scale_factor = min(units_per_em / width, units_per_em / height) * 0.7

        # Center horizontally within the advance width (not the em square)
        if advance_width == 0:
            advance_width = units_per_em

        # Calculate horizontal centering based on advance width
        translate_x = (advance_width - width) / 2 - x + special_x
        translate_x *= scale_factor

        # Vertical alignment - center at mathematical axis (approximately 0.45-0.5 of em square)
        math_axis = units_per_em * 0.3  # Mathematical axis height
        svg_center_y = y + height / 2
        translate_y = math_axis - svg_center_y * (-scale_factor)  # Adjust for Y-flip

        # Transform matrix: [scale_x, 0, 0, scale_y, translate_x, translate_y]
        return Transform(scale_factor, 0, 0, -scale_factor, translate_x, translate_y)

    # Default transform for SVGs without viewBox
    scale = 0.7 * units_per_em / 1000  # Assuming 1000x1000 SVG coordinate space
    translate_x = (advance_width - 700) / 2  # Center in advance width
    translate_y = units_per_em * 0.45  # Position at math axis
    return Transform(scale, 0, 0, -scale, translate_x, translate_y)

def add_svg_glyphs_to_font(input_font_path, output_font_path, svg_data_list):
    """
    Add SVG glyphs to a font.

    Parameters:
    - input_font_path: Path to the input font file
    - output_font_path: Path to save the output font
    - svg_data_list: List of [svg_file_path, glyph_name, unicode_hex, advance_width]
    """
    # Open the input font
    font = TTFont(input_font_path)

    # Get the font's units per em for scaling
    units_per_em = font['head'].unitsPerEm
    print(f"Font units per em: {units_per_em}")

    # Make sure necessary tables exist
    if 'glyf' not in font:
        font['glyf'] = newTable('glyf')
        font['glyf'].glyphs = {}
    if 'hmtx' not in font:
        font['hmtx'] = newTable('hmtx')
        font['hmtx'].metrics = {}

    # Initialize the cmap table if needed
    cmap_tables = [table for table in font['cmap'].tables if table.isUnicode()]
    if not cmap_tables:
        raise ValueError("Font doesn't have Unicode cmap tables")

    # Get x-height if available (to help with vertical positioning)
    x_height = units_per_em * 0.45  # Default math axis position
    if 'OS/2' in font and hasattr(font['OS/2'], 'sxHeight') and font['OS/2'].sxHeight > 0:
        x_height = font['OS/2'].sxHeight

    # Process each SVG
    for svg_file_path, glyph_name, unicode_hex, advance_width, special_x in svg_data_list:
        print(f"Processing {svg_file_path} -> {glyph_name} (U+{unicode_hex})")

        # Check if the SVG file exists
        if not os.path.exists(svg_file_path):
            print(f"Error: SVG file {svg_file_path} not found")
            continue

        # Determine advance width if not specified
        if advance_width == 0:
            advance_width = int(units_per_em * 0.6)  # Default 60% of em

        # Read the SVG file as bytes to avoid encoding issues
        try:
            with open(svg_file_path, 'rb') as f:
                svg_data = f.read()

            # Convert to string for processing but preserve binary for SVGPath
            svg_text = svg_data.decode('utf-8', errors='ignore')
            cleaned_svg = clean_svg_content(svg_text)

            # Calculate transformation based on SVG viewBox and desired advance width
            transform = calculate_transform(svg_text, units_per_em, advance_width, special_x)
            print(f"Using transform: {transform}")

            # Parse the SVG with the transformation
            svg_path = SVGPath.fromstring(cleaned_svg.encode('utf-8'), transform=transform)
        except Exception as e:
            print(f"Error processing SVG: {e}")
            continue

        # Convert to glyph outline
        pen = TTGlyphPen(font.getGlyphSet())
        svg_path.draw(pen)
        glyph = pen.glyph()

        # Check if the glyph was created properly
        if not hasattr(glyph, 'coordinates') or len(glyph.coordinates) == 0:
            print(f"Warning: No points found in glyph for {svg_file_path}")

        # Add the glyph to the font
        font['glyf'][glyph_name] = glyph

        # Update the glyph order
        glyph_order = font.getGlyphOrder()
        if glyph_name not in glyph_order:
            font.setGlyphOrder(glyph_order + [glyph_name])

        # Add to Unicode cmap
        unicode_int = int(unicode_hex, 16)
        for table in cmap_tables:
            table.cmap[unicode_int] = glyph_name

        # Set metrics
        font['hmtx'][glyph_name] = (advance_width, 0)

        print(f"Added glyph '{glyph_name}' (bounds: {getattr(glyph, 'xMin', 'N/A')},{getattr(glyph, 'yMin', 'N/A')},{getattr(glyph, 'xMax', 'N/A')},{getattr(glyph, 'yMax', 'N/A')}) with advance width {advance_width}")

    # Make sure we save as TTF, not WOFF2
    font.flavor = None

    # Update maxp table
    if 'maxp' in font:
        font['maxp'].numGlyphs = len(font.getGlyphOrder())

    # Set flag to recalculate bounding boxes on save
    font.recalcBBoxes = True

    # Save the modified font
    font.save(output_font_path)
    print(f"Font saved to {output_font_path}")

if __name__ == "__main__":
    # Define SVG files, glyph names, Unicode code points, and advance widths
    svg_data_list = [
        ["new_cup.svg", "cup", "222A", 600, 0],  # Using explicit advance width
        ["element_of_3.svg", "element_of", "2208", 600, 0],
        ["new_infinity.svg", "infinity", "221E", 600, 0]
    ]

    # Run the conversion
    add_svg_glyphs_to_font("Excalifont-Regular.woff2", "excalimath.ttf", svg_data_list)
