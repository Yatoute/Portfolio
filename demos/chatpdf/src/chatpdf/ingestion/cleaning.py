from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from langchain_core.documents import Document

from chatpdf.utils.pipeline import Pipeable

@dataclass(frozen=True)
class CleaningOptions:
    fix_hyphenation: bool = True          # "density-\nbased" -> "density-based"
    normalize_newlines: bool = True 
    collapse_whitespace: bool = True      # espaces multiples -> 1

class TextCleaner(Pipeable):
    def __init__(self, options: Optional[CleaningOptions] = None) -> None:
        self.options = options or CleaningOptions()

        # césure: lettre + - + newline + lettre  => on supprime "-\n"
        self._hyphen_re = re.compile(r"(?<=\w)-\n(?=\w)")

        # remplace les newlines simples (pas doubles) par espace
        # on protège d'abord les paragraphes \n\n avec un token
        self._para_token = "<<<PARA>>>"

        self._multi_space_re = re.compile(r"[ \t]{2,}")
        self._space_newline_re = re.compile(r"[ \t]*\n[ \t]*")
    
    def __call__(self, input_docs: List[Document], **kwargs) -> List[Document]:
        return self.clean_docs(input_docs)

    def clean_docs(self, docs: List[Document]) -> List[Document]:
        out: List[Document] = []
        for d in docs:
            d.page_content = self.clean_text(d.page_content)
            out.append(d)
        return out

    def clean_text(self, text: str) -> str:
        if not text:
            return text

        if self.options.fix_hyphenation:
            text = self._hyphen_re.sub("", text)

        if self.options.normalize_newlines:
            # protège les paragraphes
            text = text.replace("\n\n", self._para_token)

            # normalise les \n simples -> espace (en évitant de coller des mots)
            text = self._space_newline_re.sub(" ", text)

            # restaure les paragraphes
            text = text.replace(self._para_token, "\n\n")

        if self.options.collapse_whitespace:
            text = self._multi_space_re.sub(" ", text)

        return text.strip()


