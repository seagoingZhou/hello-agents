"""
AutoGen 软件开发团队协作案例 — 支持对话记录、状态保存/恢复、各 Agent 产出物提取（流式即时保存）
"""

import os
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.ui import Console
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.base._task import TaskResult

# ----- 输出目录 -----
OUTPUT_DIR = Path(__file__).parent / "output"
CONVERSATION_FILE = OUTPUT_DIR / "conversation_log.json"
STATE_FILE = OUTPUT_DIR / "team_state.json"
ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"


def create_openai_model_client():
    return OpenAIChatCompletionClient(
        model=os.getenv("LLM_MODEL_ID"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        model_info={
            "function_calling": True,
            "max_tokens": 4096,
            "context_length": 32768,
            "vision": False,
            "json_output": True,
            "family": "deepseek",
            "structured_output": True,
        }
    )


def create_product_manager(model_client):
    system_message = """你是一位经验丰富的产品经理，专门负责软件产品的需求分析和项目规划。

你的核心职责包括：
1. **需求分析**：深入理解用户需求，识别核心功能和边界条件
2. **技术规划**：基于需求制定清晰的技术实现路径
3. **风险评估**：识别潜在的技术风险和用户体验问题
4. **协调沟通**：与工程师和其他团队成员进行有效沟通

当接到开发任务时，请按以下结构进行分析：
1. 需求理解与分析
2. 功能模块划分
3. 技术选型建议
4. 实现优先级排序
5. 验收标准定义

请简洁明了地回应，并在分析完成后说"请工程师开始实现"。"""

    return AssistantAgent(
        name="ProductManager",
        model_client=model_client,
        system_message=system_message,
    )


def create_engineer(model_client):
    system_message = """你是一位资深的软件工程师，擅长 Python 开发和 Web 应用构建。

你的技术专长包括：
1. **Python 编程**：熟练掌握 Python 语法和最佳实践
2. **Web 开发**：精通 Streamlit、Flask、Django 等框架
3. **API 集成**：有丰富的第三方 API 集成经验
4. **错误处理**：注重代码的健壮性和异常处理

当收到开发任务时，请：
1. 仔细分析技术需求
2. 选择合适的技术方案
3. 编写完整的代码实现
4. 添加必要的注释和说明
5. 考虑边界情况和异常处理

请提供完整的可运行代码，并在完成后说"请代码审查员检查"。"""

    return AssistantAgent(
        name="Engineer",
        model_client=model_client,
        system_message=system_message,
    )


def create_code_reviewer(model_client):
    system_message = """你是一位经验丰富的代码审查专家，专注于代码质量和最佳实践。

你的审查重点包括：
1. **代码质量**：检查代码的可读性、可维护性和性能
2. **安全性**：识别潜在的安全漏洞和风险点
3. **最佳实践**：确保代码遵循行业标准和最佳实践
4. **错误处理**：验证异常处理的完整性和合理性

审查流程：
1. 仔细阅读和理解代码逻辑
2. 检查代码规范和最佳实践
3. 识别潜在问题和改进点
4. 提供具体的修改建议
5. 评估代码的整体质量

请提供具体的审查意见，完成后说"代码审查完成，请用户代理测试"。"""

    return AssistantAgent(
        name="CodeReviewer",
        model_client=model_client,
        system_message=system_message,
    )


def create_user_proxy():
    return UserProxyAgent(
        name="UserProxy",
        description="""用户代理，负责以下职责：
1. 代表用户提出开发需求
2. 执行最终的代码实现
3. 验证功能是否符合预期
4. 提供用户反馈和建议

完成测试后请回复 TERMINATE。""",
    )


# ---------- 状态持久化 ----------

def save_team_state(team: RoundRobinGroupChat, agents: Dict[str, AssistantAgent], filepath: Path):
    state = {
        "team": team.save_state(),
        "agents": {name: agent.save_state() for name, agent in agents.items()},
        "saved_at": datetime.now().isoformat(),
    }
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str))


def load_team_state(filepath: Path) -> Optional[Dict]:
    if not filepath.exists():
        return None
    return json.loads(filepath.read_text())


def restore_team_state(team: RoundRobinGroupChat, agents: Dict[str, AssistantAgent], state: Dict):
    team.load_state(state["team"])
    for name, agent in agents.items():
        if name in state["agents"]:
            agent.load_state(state["agents"][name])


# ---------- 流式即时保存 ----------

def extract_code_blocks(content: str) -> list[str]:
    blocks = []
    for match in re.finditer(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL):
        blocks.append(match.group(1).strip())
    return blocks


class StreamSaver:
    """消费流事件，每当 Agent 产出消息时即时保存"""

    def __init__(self, team_chat: RoundRobinGroupChat, agents: Dict[str, AssistantAgent]):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

        self.team_chat = team_chat
        self.agents = agents
        self.messages: list[dict] = []
        self._agent_code_counters: Dict[str, int] = {}

    def on_text_message(self, msg: TextMessage):
        """每条 TextMessage 到达时立即调用"""
        entry = {
            "source": msg.source,
            "content": msg.content,
            "type": msg.type,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        self.messages.append(entry)

        # 1. 追加到该 Agent 的完整响应文件
        agent_file = ARTIFACTS_DIR / f"{msg.source}_full_response.md"
        timestamp = msg.created_at.strftime("%H:%M:%S") if msg.created_at else "--"
        with open(agent_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}]\n{msg.content}\n\n")

        # 2. 如果有代码块，提取保存
        code_blocks = extract_code_blocks(msg.content)
        if code_blocks:
            counter = self._agent_code_counters.get(msg.source, 0)
            for code in code_blocks:
                counter += 1
                code_file = ARTIFACTS_DIR / f"{msg.source}_code_{counter}.py"
                code_file.write_text(code, encoding="utf-8")
                print(f"📄 提取到代码: {code_file}")
            self._agent_code_counters[msg.source] = counter

        # 3. 增量更新对话日志 JSON
        log = {
            "saved_at": datetime.now().isoformat(),
            "total_messages": len(self.messages),
            "messages": self.messages,
        }
        CONVERSATION_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2))

        # 4. 更新团队状态（用于断点续跑）
        self._save_state_checkpoint()

    def _save_state_checkpoint(self):
        """每次消息后保存状态，保证中断可恢复"""
        save_team_state(self.team_chat, self.agents, STATE_FILE)

    def summary(self):
        print(f"\n📦 产出物保存到: {OUTPUT_DIR.resolve()}")
        print(f"   - 对话日志: {CONVERSATION_FILE.name} ({len(self.messages)} 条)")
        print(f"   - 状态存档: {STATE_FILE.name}")
        print(f"   - 各Agent产出: {ARTIFACTS_DIR.name}/")


# ---------- 主流程 ----------

async def run_software_development_team(resume: bool = False):
    print("🔧 正在初始化模型客户端...")
    model_client = create_openai_model_client()

    print("👥 正在创建智能体团队...")

    product_manager = create_product_manager(model_client)
    engineer = create_engineer(model_client)
    code_reviewer = create_code_reviewer(model_client)
    user_proxy = create_user_proxy()

    agents = {
        "ProductManager": product_manager,
        "Engineer": engineer,
        "CodeReviewer": code_reviewer,
    }

    termination = TextMentionTermination("TERMINATE")

    team_chat = RoundRobinGroupChat(
        participants=[product_manager, engineer, code_reviewer, user_proxy],
        termination_condition=termination,
        max_turns=20,
    )

    if resume:
        state = load_team_state(STATE_FILE)
        if state:
            restore_team_state(team_chat, agents, state)
            print("✅ 从存档恢复，继续之前的协作...")
        else:
            print("❌ 无法恢复状态，将重新开始。")
            return None

    task = """我们需要开发一个比特币价格显示应用，具体要求如下：

核心功能：
- 实时显示比特币当前价格（USD）
- 显示24小时价格变化趋势（涨跌幅和涨跌额）
- 提供价格刷新功能

技术要求：
- 使用 Streamlit 框架创建 Web 应用
- 界面简洁美观，用户友好
- 添加适当的错误处理和加载状态

请团队协作完成这个任务，从需求分析到最终实现。"""

    print("🚀 启动 AutoGen 软件开发团队协作...")
    print("=" * 60)

    saver = StreamSaver(team_chat, agents)
    messages_collected: list = []

    try:
        async for event in team_chat.run_stream(task=task):
            # 实时打印到控制台
            Console.print_event(event)

            if isinstance(event, TextMessage):
                saver.on_text_message(event)
                messages_collected.append(event)
            elif isinstance(event, TaskResult):
                messages_collected = list(event.messages) if event.messages else messages_collected

    except KeyboardInterrupt:
        print("\n⏸️  协作被中断，已保存的内容不会丢失。")
        print("   使用 --resume 可从断点继续。")
    except Exception:
        # 异常时也要保存当前状态
        import traceback
        traceback.print_exc()
        print("\n⚠️  异常退出，已保存的内容不会丢失。")
        print("   使用 --resume 可从断点继续。")
    else:
        print("\n" + "=" * 60)
        print("✅ 团队协作完成！")
    finally:
        saver._save_state_checkpoint()

    saver.summary()
    return TaskResult(messages=messages_collected, stop_reason="completed")


# 主程序入口
if __name__ == "__main__":
    resume = "--resume" in sys.argv
    try:
        result = asyncio.run(run_software_development_team(resume=resume))
        if result:
            print(f"\n📋 协作结果摘要：")
            print(f"- 参与智能体数量：4个")
            msg_count = len(result.messages) if hasattr(result, 'messages') else 0
            print(f"- 消息总数：{msg_count}")
    except ValueError as e:
        print(f"❌ 配置错误：{e}")
        print("请检查 .env 文件中的配置是否正确")
    except Exception as e:
        print(f"❌ 运行错误：{e}")
        import traceback
        traceback.print_exc()
