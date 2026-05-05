from dotenv import load_dotenv
# 加载 .env 文件中的环境变量
load_dotenv()

import os
from serpapi import Client as SerpApiClient
from typing import Dict, Any

import requests
from ddgs import DDGS


def _duckduckgo_search(query: str) -> str:
    """
    基于 DuckDuckGo 的搜索引擎工具。
    完全免费，无需 API Key，中国大陆可直接访问。
    """
    print(f"🔍 正在执行 [DuckDuckGo] 网页搜索: {query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                snippets = [
                    f"[{i+1}] {r['title']}\n{r['body']}"
                    for i, r in enumerate(results)
                ]
                return "\n\n".join(snippets)
            return f"对不起，没有找到关于 '{query}' 的信息。"
    except Exception as e:
        return f"搜索时发生错误: {e}"


def search(query: str) -> str:
    """
    统一搜索入口，根据 SEARCH_ENGINE 环境变量自动切换搜索引擎。
    - duckduckgo: 使用 DuckDuckGo（免费，无需API Key）
    - bocha:      使用博查 SearchAPI（国内中文搜索）
    - serpapi:    使用 SerpApi (Google Search)
    - bing:       使用 Bing Web Search API (Azure)
    """
    engine = os.getenv("SEARCH_ENGINE", "duckduckgo").lower()
    if engine == "bing":
        return _bing_search(query)
    elif engine == "serpapi":
        return _serpapi_search(query)
    elif engine == "bocha":
        return _bocha_search(query)
    else:
        return _duckduckgo_search(query)


def _serpapi_search(query: str) -> str:
    """
    一个基于SerpApi的实战网页搜索引擎工具。
    它会智能地解析搜索结果，优先返回直接答案或知识图谱信息。
    """
    print(f"🔍 正在执行 [SerpApi] 网页搜索: {query}")
    try:
        api_key = os.getenv("SERPAPI_API_KEY")
        if not api_key:
            return "错误：SERPAPI_API_KEY 未在 .env 文件中配置。"

        params = {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "gl": "cn",  # 国家代码
            "hl": "zh-cn", # 语言代码
        }

        client = SerpApiClient(api_key=api_key)
        results = client.search(params)

        # 智能解析：优先寻找最直接的答案
        if "answer_box_list" in results:
            return "\n".join(results["answer_box_list"])
        if "answer_box" in results and "answer" in results["answer_box"]:
            return results["answer_box"]["answer"]
        if "knowledge_graph" in results and "description" in results["knowledge_graph"]:
            return results["knowledge_graph"]["description"]
        if "organic_results" in results and results["organic_results"]:
            # 如果没有直接答案，则返回前三个有机结果的摘要
            snippets = [
                f"[{i+1}] {res.get('title', '')}\n{res.get('snippet', '')}"
                for i, res in enumerate(results["organic_results"][:3])
            ]
            return "\n\n".join(snippets)

        return f"对不起，没有找到关于 '{query}' 的信息。"

    except Exception as e:
        return f"搜索时发生错误: {e}"


def _bing_search(query: str) -> str:
    """
    基于 Azure Bing Web Search API v7 的搜索引擎工具。
    中国大陆可直接访问，无需代理。
    """
    print(f"🔍 正在执行 [Bing] 网页搜索: {query}")
    try:
        api_key = os.getenv("BING_API_KEY")
        if not api_key:
            return "错误：BING_API_KEY 未在 .env 文件中配置。请前往 https://portal.azure.com 创建 Bing Search 资源。"

        endpoint = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": api_key}
        params = {"q": query, "mkt": "zh-CN", "count": 5}

        response = requests.get(endpoint, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        results = response.json()

        if "webPages" in results and results["webPages"]["value"]:
            snippets = [
                f"[{i+1}] {page.get('name', '')}\n{page.get('snippet', '')}"
                for i, page in enumerate(results["webPages"]["value"][:3])
            ]
            return "\n\n".join(snippets)

        return f"对不起，没有找到关于 '{query}' 的信息。"

    except Exception as e:
        return f"搜索时发生错误: {e}"


def _bocha_search(query: str) -> str:
    """
    基于博查 SearchAPI 的搜索引擎工具。
    国内公司，原生中文搜索，有免费额度。
    注册地址: https://open.bochaai.com/
    """
    print(f"🔍 正在执行 [博查] 网页搜索: {query}")
    try:
        api_key = os.getenv("BOCHA_API_KEY")
        if not api_key:
            return "错误：BOCHA_API_KEY 未在 .env 文件中配置。请前往 https://open.bochaai.com/ 注册获取。"

        endpoint = "https://api.bochaai.com/v1/web-search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {"query": query, "count": 5, "freshness": "noLimit"}

        response = requests.post(endpoint, headers=headers, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()

        pages = data.get("data", {}).get("webPages", {}).get("value", [])
        if pages:
            snippets = [
                f"[{i+1}] {page.get('name', '')}\n{page.get('snippet', page.get('summary', ''))}"
                for i, page in enumerate(pages[:3])
            ]
            return "\n\n".join(snippets)

        return f"对不起，没有找到关于 '{query}' 的信息。"

    except Exception as e:
        return f"搜索时发生错误: {e}"


from typing import Dict, Any

class ToolExecutor:
    """
    一个工具执行器，负责管理和执行工具。
    """
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}

    def registerTool(self, name: str, description: str, func: callable):
        """
        向工具箱中注册一个新工具。
        """
        if name in self.tools:
            print(f"警告：工具 '{name}' 已存在，将被覆盖。")
        
        self.tools[name] = {"description": description, "func": func}
        print(f"工具 '{name}' 已注册。")

    def getTool(self, name: str) -> callable:
        """
        根据名称获取一个工具的执行函数。
        """
        return self.tools.get(name, {}).get("func")

    def getAvailableTools(self) -> str:
        """
        获取所有可用工具的格式化描述字符串。
        """
        return "\n".join([
            f"- {name}: {info['description']}" 
            for name, info in self.tools.items()
        ])


# --- 工具初始化与使用示例 ---
if __name__ == '__main__':
    # 1. 初始化工具执行器
    toolExecutor = ToolExecutor()

    # 2. 注册搜索工具（根据 SEARCH_ENGINE 环境变量切换 bing/serpapi）
    search_description = "一个网页搜索引擎。当你需要回答关于时事、事实以及在你的知识库中找不到的信息时，应使用此工具。"
    toolExecutor.registerTool("Search", search_description, search)

    # 3. 打印当前搜索引擎
    engine = os.getenv("SEARCH_ENGINE", "duckduckgo")
    print(f"\n当前搜索引擎: {engine}")

    # 4. 打印可用的工具
    print("\n--- 可用的工具 ---")
    print(toolExecutor.getAvailableTools())

    # 5. 智能体的Action调用
    print("\n--- 执行 Action: Search['英伟达最新的GPU型号是什么'] ---")
    tool_function = toolExecutor.getTool("Search")
    if tool_function:
        observation = tool_function("英伟达最新的GPU型号是什么")
        print("--- 观察 (Observation) ---")
        print(observation)
    else:
        print("错误：未找到名为 'Search' 的工具。")
