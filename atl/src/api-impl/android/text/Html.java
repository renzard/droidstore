package android.text;

import android.graphics.drawable.Drawable;
import org.xml.sax.XMLReader;

public class Html {

	public static interface ImageGetter {
		public Drawable getDrawable(String source);
	}

	public static Spanned fromHtml(String source) {
		return Html.fromHtml(source, 0);
	}

	public static Spanned fromHtml(String source, int flags) {
		if (source == null)
			return new SpannableString("");
		return new SpannableString(convert(source));
	}

	/**
	 * Minimal HTML -> plain text conversion: line breaks, tag stripping and
	 * entity decoding (named, decimal &#8226; and hex &#x2022;). Spans are not
	 * generated.
	 */
	private static String convert(String src) {
		StringBuilder out = new StringBuilder(src.length());
		int n = src.length();
		for (int i = 0; i < n; i++) {
			char c = src.charAt(i);
			if (c == '<' && i + 1 < n && (Character.isLetter(src.charAt(i + 1)) || src.charAt(i + 1) == '/')) {
				int end = src.indexOf('>', i);
				if (end > i) {
					String tag = src.substring(i + 1, end).trim().toLowerCase();
					if (tag.startsWith("/"))
						tag = tag.substring(1).trim();
					if (tag.endsWith("/"))
						tag = tag.substring(0, tag.length() - 1).trim();
					int sp = 0;
					while (sp < tag.length() && Character.isLetterOrDigit(tag.charAt(sp)))
						sp++;
					String name = tag.substring(0, sp);
					if (name.equals("br") || name.equals("p") || name.equals("div") || name.equals("li")
					    || (name.length() == 2 && name.charAt(0) == 'h' && Character.isDigit(name.charAt(1)))) {
						if (out.length() > 0 && out.charAt(out.length() - 1) != '\n')
							out.append('\n');
					}
					i = end;
					continue;
				}
			} else if (c == '&') {
				int semi = src.indexOf(';', i);
				if (semi > i && semi - i <= 10) {
					String ent = src.substring(i + 1, semi);
					String dec = decodeEntity(ent);
					if (dec != null) {
						out.append(dec);
						i = semi;
						continue;
					}
				}
			}
			out.append(c);
		}
		return out.toString();
	}

	private static String decodeEntity(String ent) {
		if (ent.isEmpty())
			return null;
		if (ent.charAt(0) == '#') {
			try {
				int cp;
				if (ent.length() > 1 && (ent.charAt(1) == 'x' || ent.charAt(1) == 'X'))
					cp = Integer.parseInt(ent.substring(2), 16);
				else
					cp = Integer.parseInt(ent.substring(1));
				if (cp <= 0 || cp > 0x10FFFF)
					return null;
				return new String(Character.toChars(cp));
			} catch (NumberFormatException e) {
				return null;
			}
		}
		switch (ent) {
			case "amp": return "&";
			case "lt": return "<";
			case "gt": return ">";
			case "quot": return "\"";
			case "apos": return "'";
			case "nbsp": return "\u00a0";
			case "bull": return "\u2022";
			case "middot": return "\u00b7";
			case "hellip": return "\u2026";
			case "ndash": return "\u2013";
			case "mdash": return "\u2014";
			case "lsquo": return "\u2018";
			case "rsquo": return "\u2019";
			case "ldquo": return "\u201c";
			case "rdquo": return "\u201d";
			case "copy": return "\u00a9";
			case "reg": return "\u00ae";
			case "euro": return "\u20ac";
			default: return null;
		}
	}

	public static Spanned fromHtml(String source, ImageGetter imageGetter, TagHandler tagHandler) {
		return fromHtml(source, 0);
	}

	public static Spanned fromHtml(String source, int flags, ImageGetter imageGetter, TagHandler tagHandler) {
		return fromHtml(source, flags);
	}

	public static String toHtml(Spanned text) {
		return text.toString();
	}

	public static String escapeHtml(CharSequence source) {
		StringBuilder out = new StringBuilder(source.length());
		for (int i = 0; i < source.length(); i++) {
			char c = source.charAt(i);
			if (c == '<' || c == '>' || c == '&' || c == '"' || c == '\'' || c > 0x7F) {
				out.append("&#");
				out.append((int)c);
				out.append(';');
			} else {
				out.append(c);
			}
		}
		return out.toString();
	}

	public static interface TagHandler {
		/**
		 * This method will be called whenn the HTML parser encounters
		 * a tag that it does not know how to interpret.
		 */
		public void handleTag(boolean opening, String tag, Editable output, XMLReader xmlReader);
	}
}
