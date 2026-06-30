"""EML email document parser using the stdlib email module."""

import email
from email.header import decode_header, make_header
from email.policy import default
from pathlib import Path

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class EmailParser(BaseParser):
    """Parse .eml files (RFC 822 / MIME), extracting subject, body, attachments.

    Plain-text body is preferred over HTML; if only HTML is present, it is
    included as-is (downstream chunker will tolerate the tags). Attachment
    names are listed in a final section so the RAG can answer "what files
    were attached to this email".
    """

    def extensions(self) -> list[str]:
        return ["eml"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="email",
            display_name="邮件 (EML)",
            description="解析 .eml 邮件文件，提取主题、正文与附件名清单",
            category="email",
            extensions=["eml"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        with file_path.open("rb") as f:
            msg = email.message_from_binary_file(f, policy=default)

        subject = str(make_header(decode_header(msg.get("Subject", ""))))
        from_ = str(make_header(decode_header(msg.get("From", ""))))
        to_ = str(make_header(decode_header(msg.get("To", ""))))
        date_ = msg.get("Date", "")

        title = subject or file_path.stem

        body_text, html_text = self._extract_bodies(msg)
        attachment_names: list[str] = []
        for part in msg.walk():
            cd = part.get("Content-Disposition", "")
            if "attachment" in cd.lower():
                fn = part.get_filename()
                if fn:
                    attachment_names.append(str(make_header(decode_header(fn))))

        sections: list[ParsedSection] = []
        # Header section
        header_lines = [f"主题: {subject}", f"发件人: {from_}",
                       f"收件人: {to_}", f"日期: {date_}"]
        sections.append(ParsedSection(
            level=0, heading=title, content="\n".join(header_lines),
        ))
        # Body section
        body_content = body_text or html_text or ""
        if body_content.strip():
            sections.append(ParsedSection(
                level=1, heading="正文", content=body_content,
            ))
        # Attachments section
        if attachment_names:
            sections.append(ParsedSection(
                level=1, heading="附件清单",
                content="\n".join(f"- {n}" for n in attachment_names),
            ))

        return ParsedDocument(
            title=title, file_type="eml", sections=sections,
            metadata={
                "from": from_, "to": to_, "date": date_,
                "attachment_count": len(attachment_names),
                "has_html_body": bool(html_text),
            },
        )

    def _extract_bodies(self, msg) -> tuple[str, str]:
        """Return (plain_text, html_text). Prefers text/plain over text/html."""
        plain, html = "", ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain" and not plain:
                    plain = self._decode_payload(part)
                elif ctype == "text/html" and not html:
                    html = self._decode_payload(part)
        else:
            ctype = msg.get_content_type()
            body = self._decode_payload(msg)
            if ctype == "text/html":
                html = body
            else:
                plain = body
        return plain, html

    @staticmethod
    def _decode_payload(part) -> str:
        try:
            # email.policy=default returns str directly
            payload = part.get_content()
            if isinstance(payload, str):
                return payload
            return payload.decode("utf-8", errors="replace")
        except Exception:
            try:
                return part.get_payload(decode=True).decode("utf-8", errors="replace")
            except Exception:
                return ""
