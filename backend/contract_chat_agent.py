from backend.shared.utils.contract_search_tool import ContractSearchTool
from backend.shared.utils.enhanced_contract_search_tool import EnhancedContractSearchTool
from backend.mcp.langchain_tools import MCP_CHAT_TOOLS, MCP_CHAT_TOOL_NAMES
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, MessagesState, StateGraph
# from langgraph.prebuilt import ToolNode, tools_condition
# Note: ToolNode import disabled to fix compatibility issues
# This file may need updates for newer LangGraph versions
from datetime import date

# Tools whose _run requires a real, authenticated tenant_id that must never
# come from the LLM - see the "tenant_id is deliberately NOT a field here"
# comments on ContractInput/EnhancedContractInput. execute_tools injects it
# directly into these tools' kwargs from the request's own auth context.
# The 4 MCP-backed tools (MCP_CHAT_TOOL_NAMES) are tenant-scoped the same
# way, plus also get a server-injected correlation_id - see the
# MCP_CHAT_TOOL_NAMES branch below.
_TENANT_SCOPED_TOOL_NAMES = {"ContractSearch", "EnhancedContractSearch"} | MCP_CHAT_TOOL_NAMES


def get_agent(llm):
    # Define tools/llm
    tools = [ContractSearchTool(), EnhancedContractSearchTool(), *MCP_CHAT_TOOLS]
    llm_with_tools = llm.bind_tools(tools)

    # Base system message - identical regardless of contract_id, so the
    # assistant node can extend it per-invocation with contract-selection
    # state (real, confirmed bug found live: contract_id was correctly
    # threaded all the way into the tool-execution path, but the model
    # itself was never told a contract was already selected - it only
    # sees the conversation, not config["configurable"] - so "Analyze
    # this contract" kept asking the user for a contract ID they had no
    # way to provide, even with a real one already resolved server-side).
    BASE_SYSTEM_PROMPT = (
        "You are a helpful assistant tasked with finding and explaining relevant information about internal contracts. "
        "Always explain results you get from the tools in a concise manner to not overwhelm the user but also don't be too technical. "
        "Answer questions as if you are answering to non-technical management level. "
        "Important: Be confident and accurate in your tool choice! Avoid asking follow-up questions if possible. "
        f"Today is {date.today()}"
    )

    # Node
    def assistant(state: MessagesState, config: RunnableConfig):
        # Same server-side-only trust boundary as tenant_id/contract_id in
        # execute_tools below - this only ever reads what main.py's
        # runner() put there from the authenticated request, never
        # anything the model itself could have supplied.
        configurable = (config.get("configurable") or {}) if config else {}
        contract_id = configurable.get("contract_id")
        tenant_id = configurable.get("tenant_id")
        correlation_id = configurable.get("correlation_id")
        if contract_id:
            sys_msg = SystemMessage(content=(
                BASE_SYSTEM_PROMPT
                + " The user currently has a specific contract selected in the UI (already resolved "
                "server-side - you do not have and do not need its id or filename yourself). When they "
                "refer to 'this contract', 'the contract', or ask you to analyze, summarize, or explain "
                "it with no other contract identified, call EnhancedContractSearch immediately using a "
                "relevant search term for their question - do not ask them for a contract ID or which "
                "contract they mean, the selection already scopes the search for you."
            ))
        else:
            sys_msg = SystemMessage(content=BASE_SYSTEM_PROMPT)

        response = llm_with_tools.invoke([sys_msg] + state["messages"])
        
        # Log model decision
        from backend.infrastructure.agent_audit_service import AgentAuditService
        from backend.shared.utils.logger import correlation_id_var
        
        audit_service = AgentAuditService(
            tenant_id=tenant_id, correlation_id=correlation_id
        )
        session_id = correlation_id_var.get() or "unknown_session"
        
        # Log textual rationale if present
        if response.content:
            audit_service.log_model_decision(
                rationale=response.content[:500],
                session_id=session_id,
                metadata={"has_tool_calls": bool(response.tool_calls)}
            )
        elif response.tool_calls:
            audit_service.log_model_decision(
                rationale="Agent decided to call tools.",
                session_id=session_id,
                metadata={"tool_names": [tc['name'] for tc in response.tool_calls]}
            )
            
        return {"messages": [response]}

    # Simple tool execution function (replaces ToolNode)
    def execute_tools(state: MessagesState, config: RunnableConfig):
        from langchain_core.messages import ToolMessage
        messages = state["messages"]
        last_message = messages[-1]

        # The authenticated caller's real tenant_id, threaded through here
        # from main.py's runner() via config["configurable"] (set from the
        # verified JWT, not anything client-controlled) - never from the
        # LLM. LangGraph passes `config` to any node whose signature
        # declares it, so this requires no change to how the graph is
        # invoked.
        tenant_id = (config.get("configurable") or {}).get("tenant_id") if config else None

        # The contract the user has selected in the Chat UI, if any - same
        # trust boundary and threading mechanism as tenant_id above (never
        # from the LLM; set server-side in main.py's runner() from the
        # request body's own contract_id field). None when no contract is
        # selected, in which case tools search tenant-wide as before.
        contract_id = (config.get("configurable") or {}).get("contract_id") if config else None

        # The HTTP request's own correlation_id (X-Correlation-ID, or a
        # freshly-generated one - see TracingMiddleware), threaded the same
        # way as tenant_id above rather than read from correlation_id_var
        # inside this node: LangGraph may execute sync nodes like this one
        # in a worker thread, and a contextvar set in the main request
        # coroutine is not guaranteed to propagate there. Passing it
        # explicitly through config["configurable"] (set in main.py's
        # runner()) avoids that uncertainty entirely.
        correlation_id = (config.get("configurable") or {}).get("correlation_id") if config else None

        # Simple tool execution without ToolNode
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            # Execute tools manually
            tool_messages = []
            from backend.infrastructure.agent_audit_service import AgentAuditService
            from backend.shared.utils.logger import correlation_id_var

            audit_service = AgentAuditService(
                tenant_id=tenant_id, correlation_id=correlation_id
            )
            session_id = correlation_id_var.get() or "unknown_session"

            for tool_call in last_message.tool_calls:
                # Find and execute the tool
                for tool in tools:
                    if tool.name == tool_call['name']:
                        args = dict(tool_call['args'])
                        try:
                            if tool.name in _TENANT_SCOPED_TOOL_NAMES:
                                if not tenant_id:
                                    raise ValueError(
                                        f"No authenticated tenant_id available for tool '{tool.name}' - "
                                        "refusing to run a tenant-scoped search without one"
                                    )
                                # Server-side injection, bypassing the
                                # LLM-visible args entirely - tenant_id is
                                # not a field in either tool's args_schema,
                                # so there is nothing here for the model to
                                # have supplied or overridden even if it
                                # tried; any "tenant_id" key the model put
                                # in tool_call['args'] itself is discarded
                                # by this overwrite.
                                args["tenant_id"] = tenant_id
                                if tool.name in MCP_CHAT_TOOL_NAMES:
                                    args["correlation_id"] = correlation_id
                                if tool.name == "EnhancedContractSearch" and contract_id:
                                    # Overwrite, not merge - same reasoning
                                    # as tenant_id above: EnhancedContractInput
                                    # has no contract_id field, so there is
                                    # nothing here for the model to have
                                    # supplied itself.
                                    args["contract_id"] = contract_id
                                result = tool._run(**args)
                            else:
                                result = tool.invoke(args)

                            # Log tool execution
                            audit_service.log_tool_execution(
                                tool_name=tool.name,
                                args=args,
                                result=str(result),
                                session_id=session_id,
                                status="success"
                            )

                            # Create proper ToolMessage
                            tool_message = ToolMessage(
                                content=str(result),
                                tool_call_id=tool_call['id']
                            )
                            tool_messages.append(tool_message)
                        except Exception as e:
                            # Log tool failure
                            audit_service.log_tool_execution(
                                tool_name=tool.name,
                                args=args,
                                result=str(e),
                                session_id=session_id,
                                status="failure"
                            )
                            raise
            return {"messages": tool_messages}
        return {"messages": []}

    # Graph
    builder = StateGraph(MessagesState)

    # Define nodes: these do the work
    builder.add_node("assistant", assistant)
    builder.add_node("tools", execute_tools)

    # Define edges: these determine how the control flow moves
    builder.add_edge(START, "assistant")
    
    # Simple conditional logic (replaces tools_condition)
    def should_continue(state: MessagesState):
        messages = state["messages"]
        last_message = messages[-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        return "__end__"
    
    builder.add_conditional_edges("assistant", should_continue)
    builder.add_edge("tools", "assistant")
    return builder.compile()
