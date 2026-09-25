from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, AsyncIterator

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from chatpdf.storage import DocumentRepository
from chatpdf.agent.graph import ChatPDFGraph, AgentState
from chatpdf.agent.schemas import ChatPDFOutput, Citation
from chatpdf.agent.tools import get_tool_message

__all__ = [
    "AgentState",
    "ChatPDFGraph",
    "AgentOptions",
    "ChatPDFAgent",
    "ChatPDFOutput",
    "Citation",
    "get_tool_message",
]


@dataclass(frozen=True)
class AgentOptions:
    keep_history: bool = True
    max_history_messages: int = 30

class ChatPDFAgent:
    """
    High-level facade:
    - takes an already-built chat_model (BaseChatModel) and )
    - builds + compiles ChatPDFGraph
    - exposes query()/invoke() that take a user string
    - optionally keeps conversation history in memory
    """

    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        document_repository: DocumentRepository,
        options: Optional[AgentOptions] = None,
    ) -> None:
        self.options = options or AgentOptions()

        self._state: AgentState = AgentState(
            messages=[], 
            summary="",
            retrieval_rounds=0,
            max_retrieval_rounds=2,
        )
        self.graph = ChatPDFGraph(chat_model=chat_model, document_repository=document_repository)
        self.graph.build()
        self._app = self.graph.compile()
    
    @property
    def state(self) -> AgentState:
        return self._state
    
    def reset(self) -> None:
        self._messages = []

    def invoke(self, user_query: str) -> str:
        """Run the agent with a user query"""
        user_msg = HumanMessage(content=user_query)
        self._state["retrieval_rounds"] = 0

        if self.options.keep_history:
            self._state["messages"].append(user_msg)
            input_state = self._state
        else:
            input_state = self._state.copy()
            input_state["messages"] = [user_msg]
            input_state["summary"] = ""

        out_state = self._app.invoke(input_state)

        last_msg = out_state["messages"][-1]
        answer = getattr(last_msg, "content", str(last_msg))

        if self.options.keep_history:
            self._state = out_state
            
        return answer

    async def astream_events(self, user_query: str) -> AsyncIterator[dict]:
        """
        Yields:
            {"type": "tool", "name": "fetch_page", "args": {...}}   # tool call en cours
            {"type": "token", "content": "..."}                      # token de la réponse finale
        """
        user_msg = HumanMessage(content=user_query)
        self._state["retrieval_rounds"] = 0
        
        if self.options.keep_history:
            self._state["messages"].append(user_msg)
            input_state = self._state
        else:
            input_state = self._state.copy()
            input_state["messages"] = [user_msg]
            input_state["summary"] = ""

        final_state = None

        async for event in self._app.astream_events(input_state, version="v2"):
            kind = event["event"]

            # Token de réponse texte
            if kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and chunk.content and not getattr(chunk, "tool_call_chunks", None):
                    yield {"type": "token", "content": chunk.content}

            # Tool call déclenché
            elif kind == "on_tool_start":
                tool_name = event["name"]
                tool_args = event["data"].get("input", {})
                yield {"type": "tool", "content": get_tool_message(tool_name, tool_args)}

            # State final
            elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                final_state = event["data"].get("output")

        if final_state and self.options.keep_history:
            self._state = final_state