import os
import ast
import json
from datetime import datetime
from llm_client import HelloAgentsLLM
from dotenv import load_dotenv
from typing import List, Dict, Tuple

# 加载 .env 文件中的环境变量，处理文件不存在异常
try:
    load_dotenv()
except FileNotFoundError:
    print("警告：未找到 .env 文件，将使用系统环境变量。")
except Exception as e:
    print(f"警告：加载 .env 文件时出错: {e}")

# --- 1. LLM客户端定义 ---
# 假设你已经有llm_client.py文件，里面定义了HelloAgentsLLM类

# --- 2. 规划器 (Planner) 定义 ---
PLANNER_PROMPT_TEMPLATE = """
你是一个顶级的AI规划专家。你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
请确保计划中的每个步骤都是一个独立的、可执行的子任务，并且严格按照逻辑顺序排列。
你的输出必须是一个Python列表，其中每个元素都是一个描述子任务的字符串。

当前时间: {current_time}

问题: {question}

请严格按照以下格式输出你的计划，```python与```作为前后缀是必要的:
```python
["步骤1", "步骤2", "步骤3", ...]
```
"""

class Planner:
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client

    def plan(self, question: str) -> list[str]:
        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        prompt = PLANNER_PROMPT_TEMPLATE.format(current_time=current_time, question=question)
        messages = [{"role": "user", "content": prompt}]
        
        print("--- 正在生成计划 ---")
        response_text = self.llm_client.think(messages=messages) or ""
        print(f"✅ 计划已生成:\n{response_text}")
        
        try:
            plan_str = response_text.split("```python")[1].split("```")[0].strip()
            plan = ast.literal_eval(plan_str)
            return plan if isinstance(plan, list) else []
        except (ValueError, SyntaxError, IndexError) as e:
            print(f"❌ 解析计划时出错: {e}")
            print(f"原始响应: {response_text}")
            return []
        except Exception as e:
            print(f"❌ 解析计划时发生未知错误: {e}")
            return []

# --- 3. 执行器 (Executor) 定义 ---
EXECUTOR_PROMPT_TEMPLATE = """
你是一位顶级的AI执行专家。你的任务是严格按照给定的计划，一步步地解决问题。
你将收到原始问题、完整的计划、以及到目前为止已经完成的步骤和结果。
请你专注于解决“当前步骤”，并仅输出该步骤的最终答案，不要输出任何额外的解释或对话。

# 原始问题:
{question}

# 完整计划:
{plan}

# 历史步骤与结果:
{history}

# 当前步骤:
{current_step}

请仅输出针对“当前步骤”的回答:
"""

class Executor:
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client

    def execute_step(self, question: str, plan: list[str],
                     history: list, current_step: str) -> str:
        """
        执行单个步骤并返回结果。
        history 可以是 [(step_text, result), ...] 或 "步骤1: ...\n结果: ..." 字符串。
        """
        if isinstance(history, list):
            history_str = self._format_history(history)
        else:
            history_str = history if history else "无"

        prompt = EXECUTOR_PROMPT_TEMPLATE.format(
            question=question, plan=plan, history=history_str, current_step=current_step
        )
        messages = [{"role": "user", "content": prompt}]
        return self.llm_client.think(messages=messages) or ""

    def execute(self, question: str, plan: list[str]) -> str:
        history = ""
        final_answer = ""

        print("\n--- 正在执行计划 ---")
        for i, step in enumerate(plan, 1):
            print(f"\n-> 正在执行步骤 {i}/{len(plan)}: {step}")
            response_text = self.execute_step(question, plan, history, step)

            history += f"步骤 {i}: {step}\n结果: {response_text}\n\n"
            final_answer = response_text
            print(f"✅ 步骤 {i} 已完成，结果: {final_answer}")

        return final_answer

    def _format_history(self, history: list) -> str:
        if not history:
            return "无"
        return "\n".join(
            f"步骤 {i+1}: {step}\n结果: {result}"
            for i, (step, result) in enumerate(history)
        )

# --- 4. 结果验证器 (Validator) ---
VALIDATOR_PROMPT = """
你是一个严格的结果审查员。判断当前步骤的执行结果是否有效。

## 原始问题
{question}

## 当前步骤（待审查）
{step}

## 执行结果
{result}

## 已完成步骤（已验证正确）
{history}

## 判断标准
1. 结果是否直接、明确地回答了当前步骤的提问？
2. 结果是否避免了模糊用语（如"可能"、"大概"、"也许"）？
3. 结果是否与已完成步骤的结论一致？（允许补充，但不应矛盾）
4. 如果当前步骤需要计算，结果是否包含具体数值？

请严格按照以下JSON格式回复，不要包含其他内容:
{{"valid": true/false, "reason": "判断理由"}}
"""

class ResultValidator:
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client

    def validate(self, question: str, step: str,
                 result: str, history: list) -> Tuple[bool, str]:
        # 第一级: 规则快速路径
        quick = self._quick_check(result)
        if quick is not None:
            print(f"  ⚡ 规则判定: {'有效' if quick[0] else '无效'} ({quick[1]})")
            return quick

        # 第二级: LLM深度判断
        print("  🤔 规则无法判定，调用LLM深度验证...")
        return self._llm_validate(question, step, result, history)

    def _quick_check(self, result: str) -> Tuple[bool, str] | None:
        # 纯数值答案（含单位）视为有效，这是计算结果
        stripped = result.strip()
        if stripped.replace(".", "").replace("个", "").replace("元", "").replace("台", "").replace("辆", "").isdigit():
            return True, "数值答案"

        if len(stripped) < 3:
            return False, "结果为空或过短"
        signals = ["无法", "错误", "不确定", "没有找到", "对不起", "抱歉"]
        for sig in signals:
            if sig in result:
                return False, f"结果包含失败信号: '{sig}'"
        return None

    def _llm_validate(self, question, step, result, history) -> Tuple[bool, str]:
        history_str = "\n".join(
            f"步骤: {s}\n结果: {r}" for s, r in history
        ) if history else "无"
        prompt = VALIDATOR_PROMPT.format(
            question=question, step=step,
            result=result, history=history_str
        )
        response = self.llm_client.think([{"role": "user", "content": prompt}]) or "{}"
        try:
            data = json.loads(response)
            return data.get("valid", True), data.get("reason", "")
        except (json.JSONDecodeError, ValueError):
            return True, ""

# --- 5. 重规划器 (Replanner) ---
REPLANNER_PROMPT = """
你是一个规划修正专家。原始计划在执行中遇到问题，请根据已完成的工作和失败原因，生成修正后的剩余计划。

## 原始问题
{question}

## 原始完整计划
{original_plan}

## 已完成步骤（已验证正确，请勿重复规划）
{completed}

## 失败的步骤（需要替换）
{failed_step}

## 失败原因
{failure_reason}

## 你的任务
1. 分析失败原因的本质（信息缺失？前提错误？步骤分解不合理？）
2. 保持已完成步骤不变，只修正失败步骤及之后的路线
3. 如果失败是因为缺少前置信息，请在修正计划中插入获取该信息的步骤
4. 如果失败是因为前提假设错误，请基于实际发现重新规划

请严格按照以下格式输出修正后的剩余计划:
```python
["修正步骤1", "修正步骤2", ...]
```
"""

class Replanner:
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client

    def replan(self, question: str, original_plan: list[str],
               history: list, failed_step: str, failure_reason: str) -> list[str]:
        completed_str = "\n".join(
            f"{i+1}. {step} → {result}"
            for i, (step, result) in enumerate(history)
        ) if history else "无"

        prompt = REPLANNER_PROMPT.format(
            question=question,
            original_plan=original_plan,
            completed=completed_str,
            failed_step=failed_step,
            failure_reason=failure_reason,
        )
        response = self.llm_client.think([{"role": "user", "content": prompt}]) or ""

        print(f"📋 重规划响应:\n{response}")
        return self._parse_plan(response)

    def _parse_plan(self, response: str) -> list[str]:
        try:
            plan_str = response.split("```python")[1].split("```")[0].strip()
            plan = ast.literal_eval(plan_str)
            return plan if isinstance(plan, list) and plan else []
        except (ValueError, SyntaxError, IndexError) as e:
            print(f"❌ 解析重规划结果出错: {e}")
            return []

# --- 6. 动态 Plan-and-Solve 智能体 ---
class DynamicPlanAndSolveAgent:
    """
    带动态重规划机制的 Plan-and-Solve 智能体。
    在执行每个步骤后验证结果，失败时自动触发局部计划修正。
    """
    def __init__(self, llm_client: HelloAgentsLLM, max_replans: int = 3):
        self.llm_client = llm_client
        self.planner = Planner(llm_client)
        self.executor = Executor(llm_client)
        self.validator = ResultValidator(llm_client)
        self.replanner = Replanner(llm_client)
        self.max_replans = max_replans

    def run(self, question: str) -> str:
        print(f"\n{'='*60}")
        print(f"动态Plan-and-Solve智能体启动")
        print(f"{'='*60}\n问题: {question}")

        # Phase 1: 初始规划
        plan = self.planner.plan(question)
        if not plan:
            print("❌ 无法生成行动计划，任务终止。")
            return ""
        print(f"原始计划: {plan}")

        # Phase 2: 执行 + 动态重规划
        history: List[Tuple[str, str]] = []
        replan_count = 0
        idx = 0

        while idx < len(plan):
            step = plan[idx]
            print(f"\n--- 步骤 {idx+1}/{len(plan)}: {step} ---")

            # 单步执行
            result = self.executor.execute_step(question, plan, history, step)
            print(f"📤 结果: {result[:100]}{'...' if len(result)>100 else ''}")

            # 结果验证
            is_valid, reason = self.validator.validate(
                question, step, result, history
            )

            if is_valid:
                print(f"✅ 步骤通过验证")
                history.append((step, result))
                idx += 1
                continue

            # 验证失败
            print(f"⚠️ 步骤未通过验证: {reason}")

            if replan_count >= self.max_replans:
                print(f"🔴 已达最大重规划次数({self.max_replans})，强制接受当前结果")
                history.append((step, result))
                idx += 1
                continue

            # 触发重规划
            print(f"🔄 第 {replan_count+1} 次重规划...")
            revised = self.replanner.replan(
                question=question,
                original_plan=plan,
                history=history,
                failed_step=step,
                failure_reason=reason,
            )

            if not revised:
                print("⚠️ 重规划失败，跳过当前步骤继续")
                idx += 1
                continue

            # 拼接新计划
            plan = plan[:idx] + revised
            replan_count += 1
            print(f"📋 修正后计划 ({replan_count}/{self.max_replans}): {plan}")
            # idx 不变，用修正后的新步骤重试当前位置

        # Phase 3: 合成最终答案
        print(f"\n{'='*60}\n执行完成，共{len(history)}步，重规划{replan_count}次")
        return self._synthesize(question, history)

    def _synthesize(self, question: str, history: list) -> str:
        if not history:
            return "无法完成任务。"

        history_str = "\n".join(
            f"步骤 {i+1}: {step}\n结果: {result}"
            for i, (step, result) in enumerate(history)
        )
        prompt = f"""
基于以下步骤的执行结果，回答原始问题。

原始问题: {question}

执行过程:
{history_str}

请给出简洁的最终答案:
"""
        print("🧠 正在合成最终答案...")
        final = self.llm_client.think([{"role": "user", "content": prompt}]) or ""
        print(f"✅ 最终答案:\n{final}")
        return final

# --- 7. 原始智能体 (Agent) 整合 ---
class PlanAndSolveAgent:
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client
        self.planner = Planner(self.llm_client)
        self.executor = Executor(self.llm_client)

    def run(self, question: str):
        print(f"\n--- 开始处理问题 ---\n问题: {question}")
        plan = self.planner.plan(question)
        if not plan:
            print("\n--- 任务终止 --- \n无法生成有效的行动计划。")
            return
        final_answer = self.executor.execute(question, plan)
        print(f"\n--- 任务完成 ---\n最终答案: {final_answer}")

# --- 8. 主函数入口 ---
if __name__ == '__main__':
    try:
        llm_client = HelloAgentsLLM()

        # 选择使用哪个Agent: "original" 或 "dynamic"
        mode = "dynamic"

        if mode == "original":
            print("=" * 60)
            print("使用原始 PlanAndSolveAgent")
            print("=" * 60)
            agent = PlanAndSolveAgent(llm_client)
            question = "一个水果店周一卖出了15个苹果。周二卖出的苹果数量是周一的两倍。周三卖出的数量比周二少了5个。请问这三天总共卖出了多少个苹果？"
            agent.run(question)
        else:
            print("=" * 60)
            print("使用 DynamicPlanAndSolveAgent")
            print("=" * 60)
            agent = DynamicPlanAndSolveAgent(llm_client, max_replans=2)
            question = "一个水果店周一卖出了15个苹果。周二卖出的苹果数量是周一的两倍。周三卖出的数量比周二少了5个。请问这三天总共卖出了多少个苹果？"
            final = agent.run(question)
            print(f"\n🎉 最终输出: {final}")

    except ValueError as e:
        print(e)
