"""Outlook .msg email parser using extract-msg."""

from pathlib import Path

import extract_msg

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class MsgParser(BaseParser):
    """Parse Outlook .msg files, extracting subject/sender/body/attachments."""

    def extensions(self) -> list[str]:
        return ["msg"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="msg",
            display_name="Outlook 邮件 (MSG)",
            description="解析 Outlook .msg 文件，提取主题、发件人、正文与附件清单",
            category="email",
            extensions=["msg"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        msg = extract_msg.Message(str(file_path))

        subject = msg.subject or file_path.stem
        sender = msg.sender or ""
        to = msg.to or ""
        cc = msg.cc or ""
        date_ = msg.date or ""
        body = msg.body or ""

        # List attachment names
        attachment_names: list[str] = []
        try:
            for att in msg.attachments:
                if att.longFilename:
                    attachment_names.append(att.longFilename)
                elif att.shortFilename:
                    attachment_names.append(att.shortFilename)
        except Exception:
            pass

        msg.close()

        title = subject or file_path.stem

        sections: list[ParsedSection] = []
        # Header section
        header_lines = [
            f"主题: {subject}", f"发件人: {sender}",
            f"收件人: {to}", f"抄送: {cc}", f"日期: {date_}",
        ]
        sections.append(ParsedSection(
            level=0, heading=title, content="\n".join(header_lines),
        ))
        # Body section
        if body.strip():
            sections.append(ParsedSection(
                level=1, heading="正文", content=body,
            ))
        # Attachments section
        if attachment_names:
            sections.append(ParsedSection(
                level=1, heading="附件清单",
                content="\n".join(f"- {n}" for n in attachment_names),
            ))

        return ParsedDocument(
            title=title, file_type="msg", sections=sections,
            metadata={
                "from": sender, "to": to, "cc": cc, "date": date_,
                "attachment_count": len(attachment_names),
            },
        )
