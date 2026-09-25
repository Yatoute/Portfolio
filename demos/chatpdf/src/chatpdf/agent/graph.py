from __future__ import annotations

import logging
import operator
from typing import Annotated, TypedDict, List

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import RemoveMessage, add_messages
from langgraph.prebuilt import ToolNode

from chatpdf.storage import DocumentRepository
from chatpdf.agent.tools import ChatPDFTools
from chatpdf.agent.prompts import CHAT_PDF_PROMPT, FINAL_ANSWER_PROMPT, SUMMARIZE_PROMPT


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    summary: str
    retrieval_rounds:Annotated[int, operator.add] = 0
    max_retrieval_rounds: int=2


class ChatPDFGraph(StateGraph):
    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        document_repository: DocumentRepository,
    ):
        super().__init__(AgentState)
        
        self.model = chat_model
        self.tools = ChatPDFTools(document_repository).build()
        self.model_with_tools = self.model.bind_tools(self.tools)
        self._tool_node = ToolNode(tools=self.tools, messages_key="messages")

    @property
    def tool_node(self) -> ToolNode:
        return self._tool_node
    
    def call_llm(self, state: AgentState) -> AgentState:
        """=== Invoke the LLM ==="""
        
        logging.info("Calling LLM...")
        
        history_summary = state.get("summary", "")
        
        if history_summary:
            context = f"EXISTING MEMORY SUMMARY :\n{history_summary}\n\n"
        else:
            context = ""

        if state["retrieval_rounds"] < state["max_retrieval_rounds"]:
            prompt = CHAT_PDF_PROMPT.invoke({"context": context, "messages": state["messages"]})
            response = self.model_with_tools.invoke(prompt)
        else:
            logging.info("→ Provide final answer")
            prompt = FINAL_ANSWER_PROMPT.invoke({"context": context, "messages": state["messages"]})
            response = self.model.invoke(prompt)

        response.response_metadata = {}
        return {"messages": [response], "retrieval_rounds": 1}

    def summarize_history(self, state: AgentState) -> AgentState:
        logging.info("--- NODE: summarize_node ---")

        KEEP_LAST = 4  # TODO: make this configurable
        messages = state.get("messages", [])
        summary = state.get("summary", "")

        prompt = SUMMARIZE_PROMPT.invoke({"user_instruction":  messages[:-KEEP_LAST]})
        response = self.model.invoke(prompt)
        
        # remove all but last `KEEP_LAST` messages
        ids_to_remove = [m.id for m in messages[:-KEEP_LAST]]
        delete_ops = [RemoveMessage(id=mid) for mid in ids_to_remove if mid]

        return {"summary": f"{summary}\n\n{response.content}", "messages": delete_ops}
    
    def clean_history(self, state: AgentState) -> AgentState:
        logging.info("--- NODE: clean_history ---")
        
        state["retrieval_rounds"] = 0
        
        messages = state.get("messages", [])
        
        ids_to_remove = []
        
        for msg in messages:
            if isinstance(msg, ToolMessage) or (isinstance(msg, AIMessage) and msg.tool_calls):
                ids_to_remove.append(msg.id)

        delete_ops = [RemoveMessage(id=mid) for mid in ids_to_remove if mid]

        return {"messages": delete_ops}

    def should_continue(self, state: AgentState) -> str:
        """=== Decide whether to retrieve context or end conversation ==="""
        
        logging.info("--- EDGE: should_continue ---")

        last_msg = state["messages"][-1]

        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            logging.info("→ Tools needed")
            return "continue"

        logging.info("→ No tools needed")

        return "end"
    
    def should_summarize(self, state: AgentState) -> bool:
        """=== Decide whether to summarize history ==="""
        
        logging.info("--- EDGE: should_summarize ---")

        MAX_TOKENS = 10000 # TODO: make this configurable later
        MAX_RECENT_MESSAGES = 10 # TODO: make this configurable later
        
        messages = state.get("messages")
        last_msg = messages[-1]

        if isinstance(last_msg, AIMessage) :
            
            usage_metadata = getattr(last_msg, "usage_metadata", None)
            if isinstance(usage_metadata, dict):
                total_tokens = usage_metadata.get("total_tokens", 0)
                if total_tokens >= MAX_TOKENS and len(messages) >= MAX_RECENT_MESSAGES:
                    return "summarize"
  
        return "end"

    def build(self) -> None:
        """=== Build the graph ==="""
        
        self.add_node("agent", self.call_llm)
        self.add_node("retrieve_context", self.tool_node)
        self.add_node("summarize", self.summarize_history)
        self.add_node("clean_history", self.clean_history)
        
        self.add_edge(START, "agent")
        self.add_conditional_edges(
            source="agent",
            path=self.should_continue,
            path_map={"continue": "retrieve_context", "end": "clean_history"},
        )
        self.add_edge("retrieve_context", "agent")
        self.add_conditional_edges(
            source="clean_history",
            path=self.should_summarize,
            path_map={"summarize": "summarize", "end": END},
        )
        self.add_edge("summarize", END)