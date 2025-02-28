import re
import sys
from pathlib import Path
from typing import Callable, List
from unittest.mock import mock_open

import pytest
from jinja2 import TemplateSyntaxError

from promptflow._core._errors import InvalidSource
from promptflow._core.tools_manager import ToolLoader
from promptflow._internal import tool
from promptflow._sdk.entities import CustomConnection, CustomStrongTypeConnection
from promptflow.connections import AzureOpenAIConnection
from promptflow.contracts.flow import InputAssignment, InputValueType, Node, ToolSource, ToolSourceType
from promptflow.contracts.tool import AssistantDefinition, InputDefinition, Secret, Tool, ToolType, ValueType
from promptflow.contracts.types import PromptTemplate
from promptflow.exceptions import UserErrorException
from promptflow.executor._errors import (
    ConnectionNotFound,
    InvalidConnectionType,
    NodeInputValidationError,
    ResolveToolError,
    ValueTypeUnresolved,
)
from promptflow.executor._tool_resolver import ResolvedTool, ToolResolver

from ...utils import DATA_ROOT, FLOW_ROOT

TEST_ROOT = Path(__file__).parent.parent.parent
REQUESTS_PATH = TEST_ROOT / "test_configs/executor_api_requests"
WRONG_REQUESTS_PATH = TEST_ROOT / "test_configs/executor_wrong_requests"


class MyFirstCSTConnection(CustomStrongTypeConnection):
    api_key: Secret
    api_base: str


@tool(streaming_option_parameter="stream_enabled")
def mock_package_func(prompt: PromptTemplate, **kwargs):
    for k, v in kwargs.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
    return prompt


@pytest.mark.unittest
class TestToolResolver:
    @pytest.fixture
    def resolver(self):
        return ToolResolver(working_dir=None, connections={})

    def test_resolve_tool_by_node_with_diff_type(self, resolver, mocker):
        node = mocker.Mock(name="node", tool=None, inputs={})

        mocker.patch.object(
            resolver,
            "_resolve_package_node",
            return_value=mocker.Mock(node=node, definition=None, callable=None, init_args=None),
        )
        mocker.patch.object(
            resolver,
            "_resolve_script_node",
            return_value=mocker.Mock(node=node, definition=None, callable=None, init_args=None),
        )
        mocker.patch.object(
            resolver,
            "_resolve_prompt_node",
            return_value=mocker.Mock(node=node, definition=None, callable=None, init_args=None),
        )
        mocker.patch.object(
            resolver,
            "_resolve_llm_node",
            return_value=mocker.Mock(node=node, definition=None, callable=None, init_args=None),
        )
        mocker.patch.object(
            resolver,
            "_integrate_prompt_in_package_node",
            return_value=mocker.Mock(node=node, definition=None, callable=None, init_args=None),
        )

        node.type = ToolType.PYTHON
        node.source = mocker.Mock(type=ToolSourceType.Package)
        resolver.resolve_tool_by_node(node)
        resolver._resolve_package_node.assert_called_once()

        node.type = ToolType.PYTHON
        node.source = mocker.Mock(type=ToolSourceType.Code)
        resolver.resolve_tool_by_node(node)
        resolver._resolve_script_node.assert_called_once()

        node.type = ToolType.PROMPT
        resolver.resolve_tool_by_node(node)
        resolver._resolve_prompt_node.assert_called_once()

        node.type = ToolType.LLM
        resolver.resolve_tool_by_node(node)
        resolver._resolve_llm_node.assert_called_once()

        resolver._resolve_package_node.reset_mock()
        node.type = ToolType.CUSTOM_LLM
        node.source = mocker.Mock(type=ToolSourceType.PackageWithPrompt)
        resolver.resolve_tool_by_node(node)
        resolver._resolve_package_node.assert_called_once()
        resolver._integrate_prompt_in_package_node.assert_called_once()

    def test_resolve_tool_by_node_with_invalid_type(self, resolver, mocker):
        node = mocker.Mock(name="node", tool=None, inputs={})
        node.source = mocker.Mock(type=None)

        with pytest.raises(ResolveToolError) as exec_info:
            resolver.resolve_tool_by_node(node)

        assert isinstance(exec_info.value.inner_exception, NotImplementedError)
        assert "Tool type" in exec_info.value.message

    def test_resolve_tool_by_node_with_invalid_source_type(self, resolver, mocker):
        node = mocker.Mock(name="node", tool=None, inputs={})
        node.type = ToolType.PYTHON
        node.source = mocker.Mock(type=None)

        with pytest.raises(ResolveToolError) as exec_info:
            resolver.resolve_tool_by_node(node)

        assert isinstance(exec_info.value.inner_exception, NotImplementedError)
        assert "Tool source type" in exec_info.value.message

        node.type = ToolType.CUSTOM_LLM
        node.source = mocker.Mock(type=None)
        with pytest.raises(ResolveToolError) as exec_info:
            resolver.resolve_tool_by_node(node)

        assert isinstance(exec_info.value.inner_exception, NotImplementedError)
        assert "Tool source type" in exec_info.value.message

    def test_resolve_tool_by_node_with_no_source(self, resolver, mocker):
        node = mocker.Mock(name="node", tool=None, inputs={})
        node.source = None

        with pytest.raises(ResolveToolError) as ex:
            resolver.resolve_tool_by_node(node)
        assert isinstance(ex.value.inner_exception, UserErrorException)

    def test_resolve_tool_by_node_with_no_source_path(self, resolver, mocker):
        node = mocker.Mock(name="node", tool=None, inputs={})
        node.type = ToolType.PROMPT
        node.source = mocker.Mock(type=ToolSourceType.Package, path=None)

        with pytest.raises(ResolveToolError) as exec_info:
            resolver.resolve_tool_by_node(node)

        assert isinstance(exec_info.value.inner_exception, InvalidSource)
        assert "Node source path" in exec_info.value.message

    def test_resolve_tool_by_node_with_duplicated_inputs(self, resolver, mocker):
        node = mocker.Mock(name="node", tool=None, inputs={})
        node.type = ToolType.PROMPT
        mocker.patch.object(resolver, "_load_source_content", return_value="{{template}}")

        with pytest.raises(ResolveToolError) as exec_info:
            resolver.resolve_tool_by_node(node)

        assert isinstance(exec_info.value.inner_exception, NodeInputValidationError)
        assert "These inputs are duplicated" in exec_info.value.message

    def test_resolve_tool_by_node_with_invalid_template(self, resolver, mocker):
        node = mocker.Mock(tool=None, inputs={})
        node.name = "node"
        node.type = ToolType.PROMPT
        mocker.patch.object(resolver, "_load_source_content", return_value="{{current context}}")

        with pytest.raises(ResolveToolError) as exec_info:
            resolver.resolve_tool_by_node(node)

        assert isinstance(exec_info.value.inner_exception, TemplateSyntaxError)
        expected_message = (
            "Tool load failed in 'node': Jinja parsing failed at line 1: "
            "(TemplateSyntaxError) expected token 'end of print statement', got 'context'"
        )
        assert expected_message in exec_info.value.message

    def test_convert_node_literal_input_types_with_invalid_case(self):
        # Case 1: conn_name not in connections, should raise conn_name not found error
        tool = Tool(name="mock", type="python", inputs={"conn": InputDefinition(type=["CustomConnection"])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)},
        )
        with pytest.raises(ConnectionNotFound):
            tool_resolver = ToolResolver(working_dir=None, connections={})
            tool_resolver._convert_node_literal_input_types(node, tool)

        # Case 2: conn_name in connections, but type not matched
        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        with pytest.raises(NodeInputValidationError) as exe_info:
            tool_resolver = ToolResolver(working_dir=None, connections=connections)
            tool_resolver._convert_node_literal_input_types(node, tool)
        message = "'AzureOpenAIConnection' is not supported, valid types ['CustomConnection']"
        assert message in exe_info.value.message, "Expected: {}, Actual: {}".format(message, exe_info.value.message)

        # Case 3: Literal value, type mismatch
        tool = Tool(name="mock", type="python", inputs={"int_input": InputDefinition(type=[ValueType.INT])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"int_input": InputAssignment(value="invalid", value_type=InputValueType.LITERAL)},
        )
        with pytest.raises(NodeInputValidationError) as exe_info:
            tool_resolver = ToolResolver(working_dir=None, connections={})
            tool_resolver._convert_node_literal_input_types(node, tool)
        message = "value 'invalid' is not type int"
        assert message in exe_info.value.message, "Expected: {}, Actual: {}".format(message, exe_info.value.message)

        # Case 4: Unresolved value, like newly added type not in old version ValueType enum
        tool = Tool(name="mock", type="python", inputs={"int_input": InputDefinition(type=["A_good_type"])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"int_input": InputAssignment(value="invalid", value_type=InputValueType.LITERAL)},
        )
        with pytest.raises(ValueTypeUnresolved):
            tool_resolver = ToolResolver(working_dir=None, connections={})
            tool_resolver._convert_node_literal_input_types(node, tool)

        # Case 5: Literal value, invalid image in list
        tool = Tool(name="mock", type="python", inputs={"list_input": InputDefinition(type=[ValueType.LIST])})
        invalid_image = {"data:image/jpg;base64": "invalid_image"}
        node = Node(
            name="mock",
            tool=tool,
            inputs={"list_input": InputAssignment(value=[invalid_image], value_type=InputValueType.LITERAL)},
        )
        with pytest.raises(NodeInputValidationError) as exe_info:
            tool_resolver = ToolResolver(working_dir=None, connections={})
            tool_resolver._convert_node_literal_input_types(node, tool)
        message = "Invalid base64 image"
        assert message in exe_info.value.message, "Expected: {}, Actual: {}".format(message, exe_info.value.message)

        # Case 6: Literal value, invalid assistant definition path
        tool = Tool(
            name="mock",
            type="python",
            inputs={"assistant_definition": InputDefinition(type=[ValueType.ASSISTANT_DEFINITION])},
        )
        node = Node(
            name="mock",
            tool=tool,
            inputs={"assistant_definition": InputAssignment(value="invalid_path", value_type=InputValueType.LITERAL)},
        )
        with pytest.raises(NodeInputValidationError) as exe_info:
            tool_resolver = ToolResolver(working_dir=Path(__file__).parent, connections={})
            tool_resolver._convert_node_literal_input_types(node, tool)
        assert (
            "Failed to load assistant definition" in exe_info.value.message
            and "is not a valid path" in exe_info.value.message
        ), "Expected: {}, Actual: {}".format(message, exe_info.value.message)

    def test_resolve_llm_connection_to_inputs(self):
        # Case 1: node.connection is not specified
        tool = Tool(name="mock", type="python", inputs={"conn": InputDefinition(type=["CustomConnection"])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)},
        )
        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        with pytest.raises(ConnectionNotFound):
            tool_resolver = ToolResolver(working_dir=None, connections=connections)
            tool_resolver._resolve_llm_connection_to_inputs(node, tool)

        # Case 2: node.connection is not found from connection manager
        tool = Tool(name="mock", type="python", inputs={"conn": InputDefinition(type=["CustomConnection"])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)},
            connection="conn_name1",
        )
        connections = {}
        with pytest.raises(ConnectionNotFound):
            tool_resolver = ToolResolver(working_dir=None, connections=connections)
            tool_resolver._resolve_llm_connection_to_inputs(node, tool)

        # Case 3: Tool definition with bad input type list
        tool = Tool(name="mock", type="python", inputs={"conn": InputDefinition(type=["int"])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)},
            connection="conn_name",
        )
        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        with pytest.raises(InvalidConnectionType) as exe_info:
            tool_resolver = ToolResolver(working_dir=None, connections=connections)
            tool_resolver._resolve_llm_connection_to_inputs(node, tool)
        assert "Connection type can not be resolved for tool" in exe_info.value.message

        # Case 4: Tool type not match the connection manager return
        tool = Tool(name="mock", type="python", inputs={"conn": InputDefinition(type=["OpenAIConnection"])})
        node = Node(
            name="mock",
            tool=tool,
            inputs={"conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)},
            connection="conn_name",
        )
        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        with pytest.raises(InvalidConnectionType) as exe_info:
            tool_resolver = ToolResolver(working_dir=None, connections=connections)
            tool_resolver._resolve_llm_connection_to_inputs(node, tool)
        assert "Invalid connection" in exe_info.value.message

        # Case 5: Normal case
        tool = Tool(
            name="mock",
            type="python",
            inputs={"conn": InputDefinition(type=["OpenAIConnection", "AzureOpenAIConnection"])},
        )
        node = Node(
            name="mock",
            tool=tool,
            inputs={"conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)},
            connection="conn_name",
        )
        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}

        tool_resolver = ToolResolver(working_dir=None, connections=connections)
        key, conn = tool_resolver._resolve_llm_connection_to_inputs(node, tool)
        assert key == "conn"
        assert isinstance(conn, AzureOpenAIConnection)

    def test_resolve_llm_node(self, mocker):
        def mock_llm_api_func(prompt: PromptTemplate, **kwargs):
            for k, v in kwargs.items():
                prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
            return prompt

        tool_loader = ToolLoader(working_dir=None)
        tool = Tool(name="mock", type=ToolType.LLM, inputs={"conn": InputDefinition(type=["AzureOpenAIConnection"])})
        mocker.patch.object(tool_loader, "load_tool_for_llm_node", return_value=tool)

        mocker.patch(
            "promptflow._core.tools_manager.BuiltinsManager._load_package_tool",
            return_value=(mock_llm_api_func, {"conn": AzureOpenAIConnection}),
        )

        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        tool_resolver = ToolResolver(working_dir=None, connections=connections)
        tool_resolver._tool_loader = tool_loader
        mocker.patch.object(tool_resolver, "_load_source_content", return_value="{{text}}![image]({{image}})")

        node = Node(
            name="mock",
            tool=None,
            inputs={
                "conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL),
                "text": InputAssignment(value="Hello World!", value_type=InputValueType.LITERAL),
                "image": InputAssignment(value=str(DATA_ROOT / "logo.jpg"), value_type=InputValueType.LITERAL),
            },
            connection="conn_name",
            provider="mock",
        )
        resolved_tool = tool_resolver._resolve_llm_node(node, convert_input_types=True)
        assert len(resolved_tool.node.inputs) == 2
        kwargs = {k: v.value for k, v in resolved_tool.node.inputs.items()}
        pattern = re.compile(r"^Hello World!!\[image\]\(Image\([a-z0-9]{8}\)\)$")
        prompt = resolved_tool.callable(**kwargs)
        assert re.match(pattern, prompt)

    def test_resolve_script_node(self, mocker):
        def mock_python_func(prompt: PromptTemplate, **kwargs):
            for k, v in kwargs.items():
                prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
            return prompt

        tool_loader = ToolLoader(working_dir=None)
        tool = Tool(name="mock", type=ToolType.PYTHON, inputs={"conn": InputDefinition(type=["AzureOpenAIConnection"])})
        mocker.patch.object(tool_loader, "load_tool_for_script_node", return_value=(None, tool))

        mocker.patch(
            "promptflow._core.tools_manager.BuiltinsManager._load_tool_from_module",
            return_value=(mock_python_func, {"conn": AzureOpenAIConnection}),
        )

        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        tool_resolver = ToolResolver(working_dir=None, connections=connections)
        tool_resolver._tool_loader = tool_loader

        node = Node(
            name="mock",
            tool=None,
            inputs={
                "conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL),
                "prompt": InputAssignment(value="{{text}}", value_type=InputValueType.LITERAL),
                "text": InputAssignment(value="Hello World!", value_type=InputValueType.LITERAL),
            },
            connection="conn_name",
            provider="mock",
        )
        resolved_tool = tool_resolver._resolve_script_node(node, convert_input_types=True)
        assert len(resolved_tool.node.inputs) == 2
        kwargs = {k: v.value for k, v in resolved_tool.node.inputs.items()}
        assert resolved_tool.callable(**kwargs) == "Hello World!"

    def test_resolve_script_node_with_assistant_definition(self, mocker):
        def mock_python_func(input: AssistantDefinition):
            if input.model == "model" and input.instructions == "instructions" and input.tools == []:
                return True
            return False

        tool_loader = ToolLoader(working_dir=None)
        tool = Tool(
            name="mock", type=ToolType.PYTHON, inputs={"input": InputDefinition(type=[ValueType.ASSISTANT_DEFINITION])}
        )
        mocker.patch.object(tool_loader, "load_tool_for_script_node", return_value=(None, tool))

        mocker.patch(
            "promptflow._core.tools_manager.BuiltinsManager._load_tool_from_module",
            return_value=(mock_python_func, {}),
        )

        tool_resolver = ToolResolver(working_dir=Path(__file__).parent, connections={})
        tool_resolver._tool_loader = tool_loader
        mocker.patch("builtins.open", mock_open())
        mocker.patch(
            "ruamel.yaml.YAML.load", return_value={"model": "model", "instructions": "instructions", "tools": []}
        )

        node = Node(
            name="mock",
            tool=None,
            inputs={"input": InputAssignment(value="test_tool_resolver.py", value_type=InputValueType.LITERAL)},
        )
        resolved_tool = tool_resolver._resolve_script_node(node, convert_input_types=True)
        assert len(resolved_tool.node.inputs) == 1
        kwargs = {k: v.value for k, v in resolved_tool.node.inputs.items()}
        assert resolved_tool.callable(**kwargs)

    def test_resolve_package_node(self, mocker):
        tool_loader = ToolLoader(working_dir=None)
        tool = Tool(name="mock", type=ToolType.PYTHON, inputs={"conn": InputDefinition(type=["AzureOpenAIConnection"])})
        mocker.patch.object(tool_loader, "load_tool_for_package_node", return_value=tool)

        mocker.patch(
            "promptflow._core.tools_manager.BuiltinsManager._load_package_tool",
            return_value=(mock_package_func, {"conn": AzureOpenAIConnection}),
        )

        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        tool_resolver = ToolResolver(working_dir=None, connections=connections)
        tool_resolver._tool_loader = tool_loader

        node = Node(
            name="mock",
            tool=None,
            inputs={
                "conn": InputAssignment(value="conn_name", value_type=InputValueType.LITERAL),
                "prompt": InputAssignment(value="{{text}}", value_type=InputValueType.LITERAL),
                "text": InputAssignment(value="Hello World!", value_type=InputValueType.LITERAL),
            },
            connection="conn_name",
            provider="mock",
        )
        resolved_tool = tool_resolver._resolve_package_node(node, convert_input_types=True)
        assert len(resolved_tool.node.inputs) == 2
        kwargs = {k: v.value for k, v in resolved_tool.node.inputs.items()}
        assert resolved_tool.callable(**kwargs) == "Hello World!"

    def test_integrate_prompt_in_package_node(self, mocker):
        tool_resolver = ToolResolver(working_dir=None, connections={})
        mocker.patch.object(
            tool_resolver,
            "_load_source_content",
            return_value="{{text}}",
        )

        tool = Tool(name="mock", type=ToolType.CUSTOM_LLM, inputs={"prompt": InputDefinition(type=["PromptTemplate"])})
        node = Node(
            name="mock",
            tool=None,
            inputs={"text": InputAssignment(value="Hello World!", value_type=InputValueType.LITERAL)},
            connection="conn_name",
            provider="mock",
        )
        resolved_tool = ResolvedTool(node=node, callable=mock_package_func, definition=tool, init_args=None)
        assert resolved_tool.callable._streaming_option_parameter == "stream_enabled"
        resolved_tool = tool_resolver._integrate_prompt_in_package_node(resolved_tool)
        assert resolved_tool.callable._streaming_option_parameter == "stream_enabled"
        kwargs = {k: v.value for k, v in resolved_tool.node.inputs.items()}
        assert resolved_tool.callable(**kwargs) == "Hello World!"

    @pytest.mark.parametrize(
        "conn_types, expected_type",
        [
            (["MyFirstCSTConnection"], MyFirstCSTConnection),
            (["CustomConnection", "MyFirstCSTConnection"], CustomConnection),
            (["CustomConnection", "MyFirstCSTConnection", "MySecondCSTConnection"], CustomConnection),
            (["MyFirstCSTConnection", "MySecondCSTConnection"], MyFirstCSTConnection),
        ],
    )
    def test_convert_to_custom_strong_type_connection_value(self, conn_types: List[str], expected_type, mocker):
        connections = {"conn_name": {"type": "CustomConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        tool_resolver = ToolResolver(working_dir=None, connections=connections)

        node = mocker.Mock(name="node", tool=None, inputs={})
        node.type = ToolType.PYTHON
        node.source = mocker.Mock(type=ToolSourceType.Code)
        tool = Tool(name="tool", type="python", inputs={"conn": InputDefinition(type=["CustomConnection"])})
        m = sys.modules[__name__]
        v = InputAssignment(value="conn_name", value_type=InputValueType.LITERAL)
        actual = tool_resolver._convert_to_custom_strong_type_connection_value(
            "conn_name", v, node, tool, conn_types, m
        )
        assert isinstance(actual, expected_type)
        assert actual.api_base == "mock"

    def test_load_source(self):
        # Create a mock Node object with a valid source path
        node = Node(name="mock", tool=None, inputs={}, source=ToolSource())
        node.source.path = "./script_with_special_character/script_with_special_character.py"

        resolver = ToolResolver(FLOW_ROOT)

        result = resolver._load_source_content(node)
        assert "https://www.bing.com/\ue000\ue001/" in result

    @pytest.mark.parametrize(
        "source",
        [
            None,
            ToolSource(path=None),  # Then will try to read one directory.
            ToolSource(path=""),  # Then will try to read one directory.
            ToolSource(path="NotExistPath.py"),
        ],
    )
    def test_load_source_error(self, source):
        # Create a mock Node object with a valid source path
        node = Node(name="mock", tool=None, inputs={}, source=source)
        resolver = ToolResolver(FLOW_ROOT)

        with pytest.raises(InvalidSource) as _:
            resolver._load_source_content(node)

    @pytest.mark.parametrize(
        "predefined_inputs", [({"connection": "conn_name"}), ({"connection": "conn_name", "input_int": 1})]
    )
    def test_load_tools(self, predefined_inputs):
        input_int = 1
        input_str = "test"
        tool_definitions = [
            {"type": "code_interpreter"},
            {"type": "retrieval"},
            {
                "type": "function",
                "tool_type": "python",
                "source": {"type": "code", "path": "test_assistant_tool_invoker.py"},
                "predefined_inputs": predefined_inputs,
            },
        ]

        assistant_definitions = AssistantDefinition(model="model", instructions="instructions", tools=tool_definitions)
        assistant_definitions.tools = tool_definitions
        assert assistant_definitions._tool_invoker is None

        # Test load tools
        connections = {"conn_name": {"type": "AzureOpenAIConnection", "value": {"api_key": "mock", "api_base": "mock"}}}
        tool_resolver = ToolResolver(working_dir=Path(__file__).parent, connections=connections)
        tool_resolver._resolve_assistant_tool(assistant_definitions)
        invoker = assistant_definitions._tool_invoker
        assert len(invoker._assistant_tools) == len(assistant_definitions.tools) == len(tool_definitions)
        for tool_name, assistant_tool in invoker._assistant_tools.items():
            assert tool_name in ("code_interpreter", "retrieval", "sample_tool")
            assert assistant_tool.name == tool_name
            assert isinstance(assistant_tool.openai_definition, dict)
            if tool_name in ("code_interpreter", "retrieval"):
                assert assistant_tool.func is None
            else:
                assert isinstance(assistant_tool.func, Callable)

        # Test to_openai_tools
        descriptions = invoker.to_openai_tools()
        assert len(descriptions) == len(tool_definitions)
        properties = {
            "input_int": {"description": "This is a sample input int.", "type": "number"},
            "input_str": {"description": "This is a sample input str.", "type": "string"},
        }
        required = ["input_int", "input_str"]
        self._remove_predefined_inputs(properties, predefined_inputs.keys())
        self._remove_predefined_inputs(required, predefined_inputs.keys())
        for description in descriptions:
            if description["type"] in ("code_interpreter", "retrieval"):
                assert description == {"type": description["type"]}
            else:
                assert description == {
                    "type": "function",
                    "function": {
                        "name": "sample_tool",
                        "description": "This is a sample tool.",
                        "parameters": {"type": "object", "properties": properties, "required": required},
                    },
                }

        # Test invoke tool
        kwargs = {"input_int": input_int, "input_str": input_str}
        self._remove_predefined_inputs(kwargs, predefined_inputs.keys())
        result = invoker.invoke_tool(func_name="sample_tool", kwargs=kwargs)
        assert result == (input_int, input_str)

    def _remove_predefined_inputs(self, value: any, predefined_inputs: list):
        for input in predefined_inputs:
            if input in value:
                if isinstance(value, dict):
                    value.pop(input)
                elif isinstance(value, list):
                    value.remove(input)
