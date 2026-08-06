from backend.shared.utils.contract_search_tool import ContractSearchTool
from backend.shared.utils.enhanced_contract_search_tool import EnhancedContractSearchTool
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
_TENANT_SCOPED_TOOL_NAMES = {"ContractSearch", "EnhancedContractSearch"}


def get_agent(llm):
    # Define tools/llm
    tools = [ContractSearchTool(), EnhancedContractSearchTool()]
    llm_with_tools = llm.bind_tools(tools)

    # System message
    sys_msg = SystemMessage(
        content="You are a helpful assistant tasked with finding and explaining relevant information about internal contracts. "
        "Always explain results you get from the tools in a concise manner to not overwhelm the user but also don't be too technical. "
        "Answer questions as if you are answering to non-technical management level. "
        "Important: Be confident and accurate in your tool choice! Avoid asking follow-up questions if possible. "
        f"Today is {date.today()}"
    )

    # Node
    def assistant(state: MessagesState):
        response = llm_with_tools.invoke([sys_msg] + state["messages"])
        
        # Log model decision
        from backend.infrastructure.agent_audit_service import AgentAuditService
        from backend.shared.utils.logger import correlation_id_var
        
        audit_service = AgentAuditService()
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

        # Simple tool execution without ToolNode
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            # Execute tools manually
            tool_messages = []
            from backend.infrastructure.agent_audit_service import AgentAuditService
            from backend.shared.utils.logger import correlation_id_var

            audit_service = AgentAuditService()
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
