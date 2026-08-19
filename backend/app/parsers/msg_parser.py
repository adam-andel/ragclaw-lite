# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
            display_name="parser.msg.name",
            description="parser.msg.desc",
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
            f"Subject: {subject}", f"From: {sender}",
            f"To: {to}", f"Cc: {cc}", f"Date: {date_}",
        ]
        sections.append(ParsedSection(
            level=0, heading=title, content="\n".join(header_lines),
        ))
        # Body section
        if body.strip():
            sections.append(ParsedSection(
                level=1, heading="Body", content=body,
            ))
        # Attachments section
        if attachment_names:
            sections.append(ParsedSection(
                level=1, heading="Attachments",
                content="\n".join(f"- {n}" for n in attachment_names),
            ))

        return ParsedDocument(
            title=title, file_type="msg", sections=sections,
            metadata={
                "from": sender, "to": to, "cc": cc, "date": date_,
                "attachment_count": len(attachment_names),
            },
        )
