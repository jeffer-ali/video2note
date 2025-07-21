import os
from pdb import lasti2lineno
import sys
import json
import time
import shutil
import re
import subprocess
from typing import Dict, List, Optional, Tuple
import datetime
from pathlib import Path
import random
from itertools import zip_longest

import yt_dlp
import httpx
from unsplash.api import Api as UnsplashApi
from unsplash.auth import Auth as UnsplashAuth
from dotenv import load_dotenv
from bs4 import BeautifulSoup
# import whisper
import openai
import argparse

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.asr.v20190614 import asr_client, models

# 加载环境变量
load_dotenv()

# 检查必要的环境变量
required_env_vars = {
    'OPENROUTER_API_KEY': '用于OpenRouter API',
    'OPENROUTER_API_URL': '用于OpenRouter API',
    'OPENROUTER_APP_NAME': '用于OpenRouter API',
    'OPENROUTER_HTTP_REFERER': '用于OpenRouter API',
    'UNSPLASH_ACCESS_KEY': '用于图片搜索',
    'UNSPLASH_SECRET_KEY': '用于Unsplash认证',
    'UNSPLASH_REDIRECT_URI': '用于Unsplash回调'
}

missing_env_vars = []
for var, desc in required_env_vars.items():
    if not os.getenv(var):
        missing_env_vars.append(f"  - {var} ({desc})")

if missing_env_vars:
    print("注意：以下环境变量未设置：")
    print("\n".join(missing_env_vars))
    print("\n将使用基本功能继续运行（无AI优化和图片）。")
    print("如需完整功能，请在 .env 文件中设置相应的 API 密钥。")
    print("继续处理...\n")

# 配置代理
http_proxy = os.getenv('HTTP_PROXY')
https_proxy = os.getenv('HTTPS_PROXY')
proxies = {
    'http': http_proxy,
    'https': https_proxy
} if http_proxy and https_proxy else None

# 禁用 SSL 验证（仅用于开发环境）
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

# OpenRouter configuration
openrouter_api_key = os.getenv('OPENROUTER_API_KEY')
openrouter_app_name = os.getenv('OPENROUTER_APP_NAME', 'video-note')
openrouter_http_referer = os.getenv('OPENROUTER_HTTP_REFERER', 'https://github.com')
openrouter_available = False

# 配置 OpenAI API
client = openai.OpenAI(
    api_key=openrouter_api_key,
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": openrouter_http_referer,
        "X-Title": openrouter_app_name,
    }
)

# 选择要使用的模型
# AI_MODEL = "google/gemini-pro"  # 使用 Gemini Pro 模型
AI_MODEL = "deepseek/deepseek-chat-v3-0324:free"

# Test OpenRouter connection
if openrouter_api_key:
    try:
        print(f"正在测试 OpenRouter API 连接...")
        response = client.models.list()  # 使用更简单的API调用来测试连接
        print("✅ OpenRouter API 连接测试成功")
        openrouter_available = True
    except Exception as e:
        print(f"⚠️ OpenRouter API 连接测试失败: {str(e)}")
        print("将继续尝试使用API，但可能会遇到问题")

# 检查Unsplash配置
unsplash_access_key = os.getenv('UNSPLASH_ACCESS_KEY')
unsplash_client = None

if unsplash_access_key:
    try:
        auth = UnsplashAuth(
            client_id=unsplash_access_key,
            client_secret=None,
            redirect_uri=None
        )
        unsplash_client = UnsplashApi(auth)
        print("✅ Unsplash API 配置成功")
    except Exception as e:
        print(f"❌ Failed to initialize Unsplash client: {str(e)}")

# 检查ffmpeg
ffmpeg_path = None
try:
    subprocess.run(["/opt/homebrew/bin/ffmpeg", "-version"], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE)
    print("✅ ffmpeg is available at /opt/homebrew/bin/ffmpeg")
    ffmpeg_path = "/opt/homebrew/bin/ffmpeg"
except Exception:
    try:
        subprocess.run(["ffmpeg", "-version"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
        print("✅ ffmpeg is available (from PATH)")
        ffmpeg_path = "ffmpeg"
    except Exception as e:
        print(f"⚠️ ffmpeg not found: {str(e)}")

class DownloadError(Exception):
    """自定义下载错误类"""
    def __init__(self, message: str, platform: str, error_type: str, details: str = None):
        self.message = message
        self.platform = platform
        self.error_type = error_type
        self.details = details
        super().__init__(self.message)

class VideoNoteGenerator:
    def __init__(self, output_dir: str = "temp_notes"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        self.openrouter_available = openrouter_available
        self.unsplash_client = unsplash_client
        self.ffmpeg_path = ffmpeg_path
        
        # 初始化whisper模型
        # print("正在加载Whisper模型...")
        # self.whisper_model = None
        # try:
        #     self.whisper_model = whisper.load_model("medium")
        #     print("✅ Whisper模型加载成功")
        # except Exception as e:
        #     print(f"⚠️ Whisper模型加载失败: {str(e)}")
        #     print("将在需要时重试加载")
        
        # 日志目录
        self.log_dir = os.path.join(self.output_dir, 'logs')
        os.makedirs(self.log_dir, exist_ok=True)
        
        # cookie目录
        self.cookie_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies')
        os.makedirs(self.cookie_dir, exist_ok=True)
        
        # 平台cookie文件
        self.platform_cookies = {
            'douyin': os.path.join(self.cookie_dir, 'douyin_cookies.txt'),
            'bilibili': os.path.join(self.cookie_dir, 'bilibili_cookies.txt'),
            'youtube': os.path.join(self.cookie_dir, 'youtube_cookies.txt')
        }
    
    # def _ensure_whisper_model(self) -> None:
    #     """确保Whisper模型已加载"""
    #     if self.whisper_model is None:
    #         try:
    #             print("正在加载Whisper模型...")
    #             self.whisper_model = whisper.load_model("medium")
    #             print("✅ Whisper模型加载成功")
    #         except Exception as e:
    #             print(f"⚠️ Whisper模型加载失败: {str(e)}")

    def _determine_platform(self, url: str) -> Optional[str]:
        """
        确定视频平台
        
        Args:
            url: 视频URL
            
        Returns:
            str: 平台名称 ('youtube', 'douyin', 'bilibili') 或 None
        """
        if 'youtube.com' in url or 'youtu.be' in url:
            return 'youtube'
        elif 'douyin.com' in url:
            return 'douyin'
        elif 'bilibili.com' in url:
            return 'bilibili'
        return None

    def _handle_download_error(self, error: Exception, platform: str, url: str) -> str:
        """
        处理下载错误并返回用户友好的错误消息
        
        Args:
            error: 异常对象
            platform: 平台名称
            url: 视频URL
            
        Returns:
            str: 用户友好的错误消息
        """
        error_msg = str(error)
        
        if "SSL" in error_msg:
            return "⚠️ SSL证书验证失败，请检查网络连接"
        elif "cookies" in error_msg.lower():
            return f"⚠️ {platform}访问被拒绝，可能需要更新cookie或更换IP地址"
        elif "404" in error_msg:
            return "⚠️ 视频不存在或已被删除"
        elif "403" in error_msg:
            return "⚠️ 访问被拒绝，可能需要登录或更换IP地址"
        elif "unavailable" in error_msg.lower():
            return "⚠️ 视频当前不可用，可能是地区限制或版权问题"
        else:
            return f"⚠️ 下载失败: {error_msg}"

    def _get_platform_options(self, platform: str) -> Dict:
        """获取平台特定的下载选项"""
        # 基本选项
        options = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': '%(title)s.%(ext)s'
        }
        
        if platform in self.platform_cookies and os.path.exists(self.platform_cookies[platform]):
            options['cookiefile'] = self.platform_cookies[platform]
            
        return options

    def _validate_cookies(self, platform: str) -> bool:
        """验证cookie是否有效"""
        if platform not in self.platform_cookies:
            return False
        
        cookie_file = self.platform_cookies[platform]
        return os.path.exists(cookie_file)

    def _get_alternative_download_method(self, platform: str, url: str) -> Optional[str]:
        """获取备用下载方法"""
        if platform == 'youtube':
            return 'pytube'
        elif platform == 'douyin':
            return 'requests'
        elif platform == 'bilibili':
            return 'you-get'
        return None

    def _download_with_alternative_method(self, platform: str, url: str, temp_dir: str, method: str) -> Optional[str]:
        """使用备用方法下载"""
        try:
            if method == 'you-get':
                cmd = ['you-get', '--no-proxy', '--no-check-certificate', '-o', temp_dir, url]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    # 查找下载的文件
                    files = [f for f in os.listdir(temp_dir) if f.endswith(('.mp4', '.flv', '.webm'))]
                    if files:
                        return os.path.join(temp_dir, files[0])
                raise Exception(result.stderr)
                
            elif method == 'requests':
                # 使用requests直接下载
                headers = {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                }
                
                # 首先获取页面内容
                response = httpx.get(url, headers=headers, verify=False)
                
                if response.status_code == 200:
                    # 尝试从页面中提取视频URL
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    video_url = None
                    # 查找video标签
                    video_tags = soup.find_all('video')
                    for video in video_tags:
                        src = video.get('src') or video.get('data-src')
                        if src:
                            video_url = src
                            break
                    
                    if not video_url:
                        # 尝试查找其他可能包含视频URL的元素
                        import re
                        video_patterns = [
                            r'https?://[^"\'\s]+\.(?:mp4|m3u8)[^"\'\s]*',
                            r'playAddr":"([^"]+)"',
                            r'play_url":"([^"]+)"'
                        ]
                        for pattern in video_patterns:
                            matches = re.findall(pattern, response.text)
                            if matches:
                                video_url = matches[0]
                                break
                    
                    if video_url:
                        if not video_url.startswith('http'):
                            video_url = 'https:' + video_url if video_url.startswith('//') else video_url
                        
                        # 下载视频
                        video_response = httpx.get(video_url, headers=headers, stream=True, verify=False)
                        if video_response.status_code == 200:
                            file_path = os.path.join(temp_dir, 'video.mp4')
                            with open(file_path, 'wb') as f:
                                for chunk in video_response.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            return file_path
                        
                    raise Exception(f"无法下载视频: HTTP {video_response.status_code}")
                raise Exception(f"无法访问页面: HTTP {response.status_code}")
                
            elif method == 'pytube':
                # 禁用SSL验证
                import ssl
                ssl._create_default_https_context = ssl._create_unverified_context
                
                from pytube import YouTube
                yt = YouTube(url)
                # 获取最高质量的MP4格式视频
                video = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
                if video:
                    return video.download(output_path=temp_dir)
                raise Exception("未找到合适的视频流")
                
        except Exception as e:
            print(f"备用下载方法 {method} 失败: {str(e)}")
            return None

    def _download_video(self, url: str, temp_dir: str) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
        """下载视频并返回音频文件路径和信息"""
        try:
            platform = self._determine_platform(url)
            if not platform:
                raise DownloadError("不支持的视频平台", "unknown", "platform_error")

            # 基本下载选项
            options = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                }],
                'quiet': True,
                'no_warnings': True,
            }

            # 下载视频
            for attempt in range(3):  # 最多重试3次
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        print(f"正在尝试下载（第{attempt + 1}次）...")
                        info = ydl.extract_info(url, download=True)
                        if not info:
                            raise DownloadError("无法获取视频信息", platform, "info_error")

                        # 找到下载的音频文件
                        downloaded_files = [f for f in os.listdir(temp_dir) if f.endswith('.mp3')]
                        if not downloaded_files:
                            raise DownloadError("未找到下载的音频文件", platform, "file_error")

                        audio_path = os.path.join(temp_dir, downloaded_files[0])
                        if not os.path.exists(audio_path):
                            raise DownloadError("音频文件不存在", platform, "file_error")

                        video_info = {
                            'title': info.get('title', '未知标题'),
                            'uploader': info.get('uploader', '未知作者'),
                            'description': info.get('description', ''),
                            'duration': info.get('duration', 0),
                            'platform': platform
                        }

                        print(f"✅ {platform}视频下载成功")
                        return audio_path, video_info

                except Exception as e:
                    print(f"⚠️ 下载失败（第{attempt + 1}次）: {str(e)}")
                    if attempt < 2:  # 如果不是最后一次尝试
                        print("等待5秒后重试...")
                        time.sleep(5)
                    else:
                        raise  # 最后一次失败，抛出异常

        except Exception as e:
            error_msg = self._handle_download_error(e, platform, url)
            print(f"⚠️ {error_msg}")
            return None, None

    def _transcribe_audio(self, audio_path: str) -> str:
        """转录音频"""
        try:              
            SECRET_ID = os.getenv("SECRET_ID")
            SECRET_KEY = os.getenv("SECRET_KEY")

            result = recognize_audio_from_url(audio_path, SECRET_ID, SECRET_KEY)
            return result
            
        except Exception as e:
            print(f"⚠️ 音频转录失败: {str(e)}")
            return ""

    def _organize_content(self, content: str) -> str:
        """使用AI整理内容"""
        try:
            if not self.openrouter_available:
                print("⚠️ OpenRouter API 未配置，将返回原始内容")
                return content

            # 构建系统提示词
            system_prompt = """你是一位著名的科普作家和博客作者，著作等身，屡获殊荣，尤其在内容创作领域有深厚的造诣。

请使用 4C 模型（建立联系 Connection、展示冲突 Conflict、强调改变 Change、即时收获 Catch）为转录的文字内容创建结构。

写作要求：
- 从用户的问题出发，引导读者理解核心概念及其背景
- 使用第二人称与读者对话，语气亲切平实
- 确保所有观点和内容基于用户提供的转录文本
- 如无具体实例，则不编造
- 涉及复杂逻辑时，使用直观类比
- 避免内容重复冗余
- 逻辑递进清晰，从问题开始，逐步深入

Markdown格式要求：
- 大标题突出主题，吸引眼球，最好使用疑问句
- 小标题简洁有力，结构清晰，尽量使用单词或短语
- 直入主题，在第一部分清晰阐述问题和需求
- 正文使用自然段，避免使用列表形式
- 内容翔实，避免过度简略，特别注意保留原文中的数据和示例信息
- 如有来源URL，使用文内链接形式
- 保留原文中的Markdown格式图片链接"""

            # 构建用户提示词
            final_prompt = f"""请根据以下转录文字内容，创作一篇结构清晰、易于理解的博客文章。

转录文字内容：

{content}"""

            # 调用API
            response = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": final_prompt}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            
            if response.choices:
                return response.choices[0].message.content.strip()
            
            return content

        except Exception as e:
            print(f"⚠️ 内容整理失败: {str(e)}")
            return content

    def _check_content(self, content: str) -> str:
        """使用AI检查内容"""
        try:
            if not self.openrouter_available:
                print("⚠️ OpenRouter API 未配置，将返回原始内容")
                return content

            # 构建系统提示词
            system_prompt = """请你扮演一名经验丰富、极其严谨的抖音内容审核专家，同时具备资深电商行业《广告法》合规顾问和过往违法案例分析师的专业视角。你的核心任务是，在深刻理解相关法规和历史违规案例的基础上，对转录文字进行全面、彻底的审查。

你的审查目标不仅是识别文案中是否存在敏感词和禁用词，更要洞察这些词汇在特定语境下是否构成虚假宣传、夸大事实、误导消费者、诱导消费、涉及迷信、软色情、承诺收益、暗示疗效或违反特定行业规范等违规行为。

审查将严格依据《中华人民共和国广告法》及抖音平台规则，并结合以下详细的违规词汇、行为类型及典型违法案例进行判断：

一、基本原则
真实性： 内容必须真实，不得含有虚假或引人误解的内容，不得欺骗、误导消费者。对宣传的内容（如产品功能、效果、成分、产地、价格、用途、性能、数据等）必须与实际相符。

合法性： 广告活动必须遵守法律、法规，诚实信用，公平竞争。

健康性： 广告应当以健康的表现形式表达内容，符合社会主义精神文明建设和弘扬中华民族优秀传统文化的要求。

二、核心违规类型与禁用词汇
绝对化用语与“最”/“一”/“级/极”相关词汇（重点审查夸大、虚假、误导）：

禁用词示例： 绝无仅有、顶尖、万能、销量+冠军、抄底、全国首家、极端、首选、空前绝后、绝对、世界领先、唯一、巅峰、顶峰、最、最佳、最具、最爱、最赚、最优、最优秀、最好、最大、最大程度、最高、最高级、最高档、最奢侈、最低、最低级、最低价、最底、最便宜、时尚最低价、最流行、最受欢迎、最时尚、最聚拢、最符合、最舒适、最先、最先进、最先进科学、最先进加工工艺、最先享受、最后、最后一波、最新、最新科技、最新科学、最新技术、第一、中国第一、全网第一、销量第一、排名第一、唯一、第一品牌、NO.1、TOP.1、独一无二、全国第一、一流、一天、仅此一次（一款）、最后一波、全国X大品牌之一、国家级（相关单位颁发的除外）、国家级产品、全球级、宇宙级、世界级、顶级（顶尖/尖端）、顶级工艺、顶级享受、极品、极佳（绝佳/绝对）、终极、极致。

案例启示： 即使是“中国葛粉行业第一品牌”这类修饰语，若与实际不符，或搭配虚假功效宣传，亦属违规。

“首/家/国”与品牌相关词汇（重点审查不实身份、不实成就、误导性宣传）：

禁用词示例： 首个、首选、全球首发、全国首家、全网首发、首款、首家、独家、独家配方、全国销量冠军、国家级产品、国家(国家免检）、国家领导人、填补国内空白、中国驰名（驰名商标）、国际品质、王牌、领袖品牌、世界领先、领导者、缔造者、创领品牌、领先上市、至尊、巅峰、领袖、之王、王者、冠军。

案例启示： “国酒茅台”的更名、“创办一年、成交量就已遥遥领先”的不实宣传，都表明这类词汇需有严谨的事实依据，否则极易违规。

虚假、欺诈及诱导消费词汇（重点审查内容不实、误导购买、营造抢购氛围）：

虚假词示例： 史无前例、前无古人、永久、万能、祖传、特效、无敌、纯天然、100%、高档、正品、真皮、超赚、精准。

欺诈/诱导消费词示例： 点击领奖、恭喜获奖、全民免单、点击有惊喜、点击获取、点击转身、点击试穿、点击翻转、领取奖品、非转基因更安全、秒杀、抢爆、再不抢就没了、不会更便宜了、没有他就XX、错过就没机会了、万人疯抢、全民疯抢/抢购、卖/抢疯了、首批售罄。

案例启示： 普通口罩宣传“医用级品质”、“始终静音”等都属于虚假宣传。营造“首批售罄”等抢购氛围，如果没有事实依据也属违规。

与时间有关的限定词（重点审查时效性虚假或模糊）：

要求： 限时必须有具体时限，所有团购须标明具体活动日期。

禁用词示例： 随时结束、仅此一次、随时涨价、马上降价、最后一波。

合规示例： 今日、今天、几天几夜、倒计时、趁现在、就、仅限、周末、周年庆、特惠趴、购物大趴、闪购、品牌团、精品团、单品团（必须有具体活动日期）。

疑似医疗用语（普通商品、化妆品、保健品、医疗器械等非药品类，严禁涉及医疗功效）：

严禁用于非药品类商品的词汇（包括但不限于）：

内分泌/免疫/助眠： 全面调整人体内分泌平衡、增强或提高免疫力、助眠、失眠、滋阴补阳、壮阳。

炎症/代谢/修复： 消炎、可促进新陈代谢、减少红血丝、产生优化细胞结构、修复受损肌肤、治愈（治愈系除外）、抗炎、活血、解毒、抗敏、脱敏。

减肥/排毒/杀菌： 减肥、清热解毒、清热袪湿、治疗、除菌、杀菌、抗菌、灭菌、防菌、消毒、排毒。

敏感肌肤： 防敏、柔敏、舒敏、缓敏、脱敏、褪敏、改善敏感肌肤、改善过敏现象、降低肌肤敏感度。

身体调节/疾病症状： 镇定、镇静、理气、行气、活血、生肌肉、补血、安神、养脑、益气、通脉、胃胀蠕动、利尿、驱寒解毒、调节内分泌、延缓更年期、补肾、祛风、生发。

重大疾病： 防癌、抗癌。

症状/疾病名称： 祛疤、降血压、防治高血压、治疗、改善内分泌、平衡荷尔蒙、防止卵巢及子宫的功能紊乱、去除体内毒素、吸附铅汞、除湿、润燥、治疗腋臭、治疗体臭、治疗阴臭、美容治疗、消除斑点、斑立净、无斑、治疗斑秃、逐层减退多种色斑、妊娠纹、酒糟鼻、伤口愈合、清除毒素、缓解痉挛抽搐、减轻或缓解疾病症状、丘疹、脓疱、手癣、甲癣、体癣、头癣、股癣、脚癣、脚气、鹅掌癣、花斑癣、牛皮癣、传染性湿疹、伤风感冒、经痛、肌痛、头痛、腹痛、便秘、哮喘、支气管炎、消化不良、刀伤、烧伤、烫伤、疮痈、毛囊炎、皮肤感染、皮肤面部痉挛等。

微生物/成分/器官： 细菌、真菌、念珠菌、糠秕孢子菌、厌氧菌、牙孢菌、痤疮、毛囊寄生虫等微生物名称、雌性激素、雄性激素、荷尔蒙、抗生素、激素、中草药、中枢神经。

细胞/身体状态： 细胞再生、细胞增殖和分化、免疫力、患处、疤痕、关节痛、冻疮、冻伤、皮肤细胞间的氧气交换、红肿、淋巴液、毛细血管、淋巴毒。

其他： 处方、药方、经××例临床观察具有明显效果。

案例启示： 消毒产品宣传“调理气血、化瘀消疖”等虚假功效，是典型的违规。

迷信用语（严禁宣传封建迷信内容）：

禁用词示例： 带来好运气、增强第六感、化解小人、增加事业运、招财进宝、健康富贵、提升运气、有助事业、护身、平衡正负能量、消除精神压力、调和气压、逢凶化吉、时来运转、万事亨通、旺人、旺财、助吉避凶、转富招福。

案例启示： 房地产广告宣传风水，明确属于违规。

打色情擦边球的用语（严禁低俗、软色情、违背社会公序良俗）：

禁用词示例： 零距离接触、余温、余香、身体器官描述等违背社会良好风尚的色情暗示词语。

案例启示： 杜蕾斯的高考软色情营销是典型案例。

三、特定行业深度审查（结合行业特性和具体案例）
服饰行业：

真实性和准确性： 宣传（如保暖、防晒、运动、冲锋衣、羽绒服等）性能、功能、产地、质量、成分、价格需与产品实际功能相符。赠送商品/服务需明示品种、规格、数量、期限和方式。

禁止虚假夸大： 不得宣传医疗功效、疾病治疗功能。例如，保暖内衣广告不得宣传抗寒低于零度以下的虚假范围。

社会公序良俗： 内衣广告不得使用真人或过于逼真的塑料模特进行穿戴展示。

证明要求： 涉及特殊用途（如防火、防水）需提供质检证明。

化妆品行业：

功效限定： 严格限定在《化妆品分类规则和分类目录》的26类功效范围内（清洁、卸妆、保湿、美容修饰、芳香、除臭、抗皱、紧致、舒缓、控油、去角质、爽身、染发、烫发、祛斑美白、防晒、防脱发、祛痘、滋养、修护）。其他功效宣传，或夸大、虚假宣传许可功效，均属违规。

禁用示例： 宣传“特效、高效、全效、强效、速效、速白、一洗白、XX天见效、XX周期见效、超强、激活、全方位、全面、安全、无毒、溶脂、吸脂、燃烧脂肪、瘦身、瘦脸、瘦腿、减肥、延年益寿、提高（保护）记忆力、提高肌肤抗刺激、消除、清除、化解死细胞、去（祛）除皱纹、平皱、修复断裂弹性（力）纤维、止脱、采用新型着色机理永不褪色、迅速修复受紫外线伤害的肌肤、更新肌肤、破坏黑色素细胞、阻断（阻碍）黑色素的形成、丰乳、丰胸、使乳房丰满、预防乳房松弛下垂（美乳、健美类化妆品除外）、改善（促进）睡眠、舒眠”等。

案例启示： 欧莱雅“8天肌肤犹如新生”属于虚构使用效果的虚假广告。

牙膏类：

功效限定： 仅限防龋、抑制牙菌斑、抗牙本质敏感、减轻牙龈问题、除渍增白、抗牙结石、减轻口臭等功效。

证明要求： 需留存相应功效检测报告。

禁用示例： 宣传治疗牙周炎、根治口腔疾病。

美容/保健品行业：

功效限定： 营养保健品（强化、增强、滋补、增加、改善、减少、消除、抵御、增强抵御力）；美容护肤品（美白、淡斑、祛痘、去皱、紧致、保湿、修护、补水）。

严禁： 宣传治疗疾病、替代药物、立竿见影、永久有效等。

药品类：

严格限定： 宣传必须严格按照药品说明书。

禁用词示例： 治疗、疗效显著、痊愈、迅速、有效、康复、保健。

保健器械：

功效限定： 改善、疏通、促进、增强、调整、减轻、舒缓。

严禁： 宣传治疗疾病、根治、包治百病、神奇功效。

房地产广告：

禁止承诺收益/升值： 收益稳健、保证升值、无忧保障、稳定收益、即买即收租金、升值价值、价值洼地、价值天成、投资回报、众筹、抄涨、炒股不如买房、升值潜力无限、买到即赚到。

禁止模糊时间/位置： XX分钟可达火车站/机场/高速、仅需XX分钟等以项目到达某一具体参照物的所需时间表示项目位置的词语。

禁止误导性规划： 对规划或建设中的交通、商业、文化教育设施以及其他市政条件作误导宣传。

案例启示： 搜房网承诺“保障财富投资有回报”被罚。

教育培训广告：

禁止承诺效果/通过率/就业： 记忆效率提升百倍、成绩飞跃、过目不忘、7天记住永不忘、通过率XX%、高分王者、名列前茅、缔造传奇、百分百高薪就业、国家承认。

禁止暗示与命题人关联： 命题专家联手、圈定考试范围、通往北大/清华的金钥匙。

案例启示： 尚德教育“一年学完，国家承认”实际无法颁发学历属虚假宣传。

金融广告：

禁止承诺收益/无风险： 100%本息保障、100%胜率、X%-X%年化收益率、无风险、保值增值、本息安心、稳赚、最专业、最安全。

风险提示： 必须对可能存在的风险以及风险责任承担有合理提示或警示。

案例启示： 融360因“对未来收益进行保证性承诺”被罚。

虚假宣传专利技术：

要求： 未取得专利权的，不得在广告中谎称取得专利权。禁止使用未授予专利权的专利申请和已经终止、撤销、无效的专利作广告。

案例启示： 小米“已申请46项专利”但实际未拿到属于误导。

请你针对以上所有方面，深度分析所提供的视频文案。对于识别出的每一个违规点，请：

明确指出违规的词语、短语或表达。

详细解释其为何构成违规（引用上述具体规则或案例类型）。

说明可能违反的《广告法》具体条款（如第二十八条等）或抖音平台社区规范。

提供具体的修改建议，以规避风险并符合合规要求。

如果文案中没有发现任何违规内容，请明确告知‘文案完全符合抖音平台规范和广告法要求’。请以清晰、条理分明、专业严谨的格式输出你的分析结果。"""

            # 构建用户提示词
            final_prompt = f"""请根据以下转录文字内容，生成一份结构清晰、具有洞察力的违规检查报告。

转录文字内容：

{content}"""

            # 调用API
            response = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": final_prompt}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            
            if response.choices:
                return response.choices[0].message.content.strip()
            
            return content

        except Exception as e:
            print(f"⚠️ 内容检查失败: {str(e)}")
            return content

    def split_content(self, text: str, max_chars: int = 2000) -> List[str]:
        """按段落分割文本，保持上下文的连贯性
        
        特点：
        1. 保持段落完整性：不会在段落中间断开
        2. 保持句子完整性：确保句子不会被截断
        3. 添加重叠内容：每个chunk都包含上一个chunk的最后一段
        4. 智能分割：对于超长段落，按句子分割并保持完整性
        """
        if not text:
            return []

        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = []
        current_length = 0
        last_paragraph = None  # 用于存储上一个chunk的最后一段
        
        for para in paragraphs:
            para = para.strip()
            if not para:  # 跳过空段落
                continue
            
            para_length = len(para)
            
            # 如果这是新chunk的开始，且有上一个chunk的最后一段，添加它作为上下文
            if not current_chunk and last_paragraph:
                current_chunk.append(f"上文概要：\n{last_paragraph}\n")
                current_length += len(last_paragraph) + 20  # 加上标题的长度
            
            # 如果单个段落就超过了最大长度，需要按句子分割
            if para_length > max_chars:
                # 如果当前块不为空，先保存
                if current_chunk:
                    last_paragraph = current_chunk[-1]
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                    if last_paragraph:
                        current_chunk.append(f"上文概要：\n{last_paragraph}\n")
                        current_length += len(last_paragraph) + 20
                
                # 按句子分割长段落
                sentences = re.split(r'([。！？])', para)
                current_sentence = []
                current_sentence_length = 0
                
                for i in range(0, len(sentences), 2):
                    sentence = sentences[i]
                    # 如果有标点符号，加上标点
                    if i + 1 < len(sentences):
                        sentence += sentences[i + 1]
                    
                    # 如果加上这个句子会超过最大长度，保存当前块并开始新块
                    if current_sentence_length + len(sentence) > max_chars and current_sentence:
                        chunks.append(''.join(current_sentence))
                        current_sentence = [sentence]
                        current_sentence_length = len(sentence)
                    else:
                        current_sentence.append(sentence)
                        current_sentence_length += len(sentence)
                
                # 保存最后一个句子块
                if current_sentence:
                    chunks.append(''.join(current_sentence))
            else:
                # 如果加上这个段落会超过最大长度，保存当前块并开始新块
                if current_length + para_length > max_chars and current_chunk:
                    last_paragraph = current_chunk[-1]
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                    if last_paragraph:
                        current_chunk.append(f"上文概要：\n{last_paragraph}\n")
                        current_length += len(last_paragraph) + 20
                current_chunk.append(para)
                current_length += para_length
        
        # 保存最后一个块
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        return chunks

    def _organize_long_content(self, content: str, duration: int = 0) -> str:
        """使用AI整理长文内容"""
        if not content.strip():
            return ""
        
        if not self.openrouter_available:
            print("⚠️ OpenRouter API 不可用，将返回原始内容")
            return content
        
        content_chunks = self.split_content(content)
        organized_chunks = []
        
        print(f"内容将分为 {len(content_chunks)} 个部分进行处理...")
        
        for i, chunk in enumerate(content_chunks, 1):
            print(f"正在处理第 {i}/{len(content_chunks)} 部分...")
            organized_chunk = self._organize_content(chunk)
            organized_chunks.append(organized_chunk)
    
        return "\n\n".join(organized_chunks)

    def _check_long_content(self, content: str) -> str:
        """使用AI整理长文内容"""
        if not content.strip():
            return ""
        
        if not self.openrouter_available:
            print("⚠️ OpenRouter API 不可用，将返回原始内容")
            return content
        
        content_chunks = self.split_content(content)
        checked_chunks = []
        
        print(f"内容将分为 {len(content_chunks)} 个部分进行处理...")
        
        for i, chunk in enumerate(content_chunks, 1):
            print(f"正在处理第 {i}/{len(content_chunks)} 部分...")
            checked_chunk = self._check_content(chunk)
            checked_chunks.append(checked_chunk)
    
        return "\n\n".join(checked_chunks)

    def convert_to_xiaohongshu(self, content: str) -> Tuple[str, List[str], List[str], List[str]]:
        """将博客文章转换为小红书风格的笔记，并生成标题和标签"""
        try:
            if not self.openrouter_available:
                print("⚠️ OpenRouter API 未配置，将返回原始内容")
                return content, [], [], []

            # 构建系统提示词
            system_prompt = """你是一位专业的小红书爆款文案写作大师，擅长将普通内容转换为刷屏级爆款笔记。
请将输入的内容转换为小红书风格的笔记，需要满足以下要求：

1. 标题创作（重要‼️）：
- 二极管标题法：
  * 追求快乐：产品/方法 + 只需N秒 + 逆天效果
  * 逃避痛苦：不采取行动 + 巨大损失 + 紧迫感
- 爆款关键词（必选1-2个）：
  * 高转化词：好用到哭、宝藏、神器、压箱底、隐藏干货、高级感
  * 情感词：绝绝子、破防了、治愈、万万没想到、爆款、永远可以相信
  * 身份词：小白必看、手残党必备、打工人、普通女生
  * 程度词：疯狂点赞、超有料、无敌、一百分、良心推荐
- 标题规则：
  * 字数：20字以内
  * emoji：2-4个相关表情
  * 标点：感叹号、省略号增强表达
  * 风格：口语化、制造悬念

2. 正文创作：
- 开篇设置（抓住痛点）：
  * 共情开场：描述读者痛点
  * 悬念引导：埋下解决方案的伏笔
  * 场景还原：具体描述场景
- 内容结构：
  * 每段开头用emoji引导
  * 重点内容加粗突出
  * 适当空行增加可读性
  * 步骤说明要清晰
- 写作风格：
  * 热情亲切的语气
  * 大量使用口语化表达
  * 插入互动性问句
  * 加入个人经验分享
- 高级技巧：
  * 使用平台热梗
  * 加入流行口头禅
  * 设置悬念和爆点
  * 情感共鸣描写

3. 标签优化：
- 提取4类标签（每类1-2个）：
  * 核心关键词：主题相关
  * 关联关键词：长尾词
  * 高转化词：购买意向强
  * 热搜词：行业热点

4. 整体要求：
- 内容体量：根据内容自动调整
- 结构清晰：善用分点和空行
- 情感真实：避免过度营销
- 互动引导：设置互动机会
- AI友好：避免机器味

注意：创作时要始终记住，标题决定打开率，内容决定完播率，互动决定涨粉率！"""

            # 构建用户提示词
            user_prompt = f"""请将以下内容转换为爆款小红书笔记。

内容如下：
{content}

请按照以下格式返回：
1. 第一行：爆款标题（遵循二极管标题法，必须有emoji）
2. 空一行
3. 正文内容（注意结构、风格、技巧的运用，控制在600-800字之间）
4. 空一行
5. 标签列表（每类标签都要有，用#号开头）

创作要求：
1. 标题要让人忍不住点进来看
2. 内容要有干货，但表达要轻松
3. 每段都要用emoji装饰
4. 标签要覆盖核心词、关联词、转化词、热搜词
5. 设置2-3处互动引导
6. 通篇要有感情和温度
7. 正文控制在600-800字之间

"""

            # 调用API
            response = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )
            
            if not response.choices:
                raise Exception("API 返回结果为空")

            # 处理返回的内容
            xiaohongshu_content = response.choices[0].message.content.strip()
            print(f"\n📝 API返回内容：\n{xiaohongshu_content}\n")
            
            # 提取标题（第一行）
            content_lines = xiaohongshu_content.split('\n')
            titles = []
            for line in content_lines:
                line = line.strip()
                if line and not line.startswith('#') and '：' not in line and '。' not in line:
                    titles = [line]
                    break
            
            if not titles:
                print("⚠️ 未找到标题，尝试其他方式提取...")
                # 尝试其他方式提取标题
                title_match = re.search(r'^[^#\n]+', xiaohongshu_content)
                if title_match:
                    titles = [title_match.group(0).strip()]
            
            if titles:
                print(f"✅ 提取到标题: {titles[0]}")
            else:
                print("⚠️ 未能提取到标题")
            
            # 提取标签（查找所有#开头的标签）
            tags = []
            tag_matches = re.findall(r'#([^\s#]+)', xiaohongshu_content)
            if tag_matches:
                tags = tag_matches
                print(f"✅ 提取到{len(tags)}个标签")
            else:
                print("⚠️ 未找到标签")
            
            # 获取相关图片
            images = []
            if self.unsplash_client:
                # 使用标题和标签作为搜索关键词
                search_terms = titles + tags[:2] if tags else titles
                search_query = ' '.join(search_terms)
                try:
                    images = self._get_unsplash_images(search_query, count=4)
                    if images:
                        print(f"✅ 成功获取{len(images)}张配图")
                    else:
                        print("⚠️ 未找到相关配图")
                except Exception as e:
                    print(f"⚠️ 获取配图失败: {str(e)}")
            
            return xiaohongshu_content, titles, tags, images

        except Exception as e:
            print(f"⚠️ 转换小红书笔记失败: {str(e)}")
            return content, [], [], []

    def _get_unsplash_images(self, query: str, count: int = 3) -> List[str]:
        """从Unsplash获取相关图片"""
        if not self.unsplash_client:
            print("⚠️ Unsplash客户端未初始化")
            return []
            
        try:
            # 将查询词翻译成英文以获得更好的结果
            if self.openrouter_available:
                try:
                    response = client.chat.completions.create(
                        model=AI_MODEL,
                        messages=[
                            {"role": "system", "content": "你是一个翻译助手。请将输入的中文关键词翻译成最相关的1-3个英文关键词，用逗号分隔。直接返回翻译结果，不要加任何解释。例如：\n输入：'保险理财知识'\n输出：insurance,finance,investment"},
                            {"role": "user", "content": query}
                        ],
                        temperature=0.3,
                        max_tokens=50
                    )
                    if response.choices:
                        query = response.choices[0].message.content.strip()
                except Exception as e:
                    print(f"⚠️ 翻译关键词失败: {str(e)}")
            
            # 使用httpx直接调用Unsplash API
            headers = {
                'Authorization': f'Client-ID {os.getenv("UNSPLASH_ACCESS_KEY")}'
            }
            
            # 对每个关键词分别搜索
            all_photos = []
            for keyword in query.split(','):
                response = httpx.get(
                    'https://api.unsplash.com/search/photos',
                    params={
                        'query': keyword.strip(),
                        'per_page': count,
                        'orientation': 'portrait',  # 小红书偏好竖版图片
                        'content_filter': 'high'    # 只返回高质量图片
                    },
                    headers=headers,
                    verify=False  # 禁用SSL验证
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data['results']:
                        # 获取图片URL，优先使用regular尺寸
                        photos = [photo['urls'].get('regular', photo['urls']['small']) 
                                for photo in data['results']]
                        all_photos.extend(photos)
            
            # 如果收集到的图片不够，用最后一个关键词继续搜索
            while len(all_photos) < count and query:
                response = httpx.get(
                    'https://api.unsplash.com/search/photos',
                    params={
                        'query': query.split(',')[-1].strip(),
                        'per_page': count - len(all_photos),
                        'orientation': 'portrait',
                        'content_filter': 'high',
                        'page': 2  # 获取下一页的结果
                    },
                    headers=headers,
                    verify=False
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data['results']:
                        photos = [photo['urls'].get('regular', photo['urls']['small']) 
                                for photo in data['results']]
                        all_photos.extend(photos)
                    else:
                        break
                else:
                    break
            
            # 返回指定数量的图片
            return all_photos[:count]
            
        except Exception as e:
            print(f"⚠️ 获取图片失败: {str(e)}")
            return []

    def process_video(self, url: str) -> List[str]:
        """处理视频链接，生成笔记
        
        Args:
            url (str): 视频链接
        
        Returns:
            List[str]: 生成的笔记文件路径列表
        """
        print("\n📹 正在处理视频...")
        
        # 创建临时目录
        temp_dir = os.path.join(self.output_dir, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        try:
            # 下载视频
            print("⬇️ 正在下载视频...")
            result = self._download_video(url, temp_dir)
            if not result:
                return []
                
            audio_path, video_info = result
            if not audio_path or not video_info:
                return []
                
            print(f"✅ 视频下载成功: {video_info['title']}")
            
            # 转录音频
            print("\n🎙️ 正在转录音频...")
            print("正在转录音频（这可能需要几分钟）...")
            transcript = self._transcribe_audio(audio_path)
            if not transcript:
                return []

            # 保存原始转录内容
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            original_file = os.path.join(self.output_dir, f"{timestamp}_original.md")
            with open(original_file, 'w', encoding='utf-8') as f:
                f.write(f"# {video_info['title']}\n\n")
                f.write(f"## 视频信息\n")
                f.write(f"- 作者：{video_info['uploader']}\n")
                f.write(f"- 时长：{video_info['duration']}秒\n")
                f.write(f"- 平台：{video_info['platform']}\n")
                f.write(f"- 链接：{url}\n\n")
                f.write(f"## 原始转录内容\n\n")
                f.write(transcript)

            # 整理长文版本
            print("\n📝 正在整理长文版本...")
            organized_content = self._organize_long_content(transcript, int(video_info['duration']))
            organized_file = os.path.join(self.output_dir, f"{timestamp}_organized.md")
            with open(organized_file, 'w', encoding='utf-8') as f:
                f.write(f"# {video_info['title']} - 整理版\n\n")
                f.write(f"## 视频信息\n")
                f.write(f"- 作者：{video_info['uploader']}\n")
                f.write(f"- 时长：{video_info['duration']}秒\n")
                f.write(f"- 平台：{video_info['platform']}\n")
                f.write(f"- 链接：{url}\n\n")
                f.write(f"## 内容整理\n\n")
                f.write(organized_content)
            
            # 生成小红书版本
            print("\n📱 正在生成小红书版本...")
            try:
                xiaohongshu_content, titles, tags, images = self.convert_to_xiaohongshu(organized_content)
                
                # 保存小红书版本
                xiaohongshu_file = os.path.join(self.output_dir, f"{timestamp}_xiaohongshu.md")
                
                # 写入文件
                with open(xiaohongshu_file, "w", encoding="utf-8") as f:
                    # 写入标题
                    f.write(f"# {titles[0]}\n\n")
                    
                    # 如果有图片，先写入第一张作为封面
                    if images:
                        f.write(f"![封面图]({images[0]})\n\n")
                    
                    # 写入正文内容的前半部分
                    content_parts = xiaohongshu_content.split('\n\n')
                    mid_point = len(content_parts) // 2
                    
                    # 写入前半部分
                    f.write('\n\n'.join(content_parts[:mid_point]))
                    f.write('\n\n')
                    
                    # 如果有第二张图片，插入到中间
                    if len(images) > 1:
                        f.write(f"![配图]({images[1]})\n\n")
                    
                    # 写入后半部分
                    f.write('\n\n'.join(content_parts[mid_point:]))
                    
                    # 如果有第三张图片，插入到末尾
                    if len(images) > 2:
                        f.write(f"\n\n![配图]({images[2]})")
                    
                    # 写入标签
                    if tags:
                        f.write("\n\n---\n")
                        f.write("\n".join([f"#{tag}" for tag in tags]))
                print(f"\n✅ 小红书版本已保存至: {xiaohongshu_file}")
                return [original_file, organized_file, xiaohongshu_file]
            except Exception as e:
                print(f"⚠️ 生成小红书版本失败: {str(e)}")
                import traceback
                print(f"错误详情:\n{traceback.format_exc()}")
            
            print(f"\n✅ 笔记已保存至: {original_file}")
            print(f"✅ 整理版内容已保存至: {organized_file}")
            return [original_file, organized_file]
            
        except Exception as e:
            print(f"⚠️ 处理视频时出错: {str(e)}")
            return []
        
        finally:
            # 清理临时文件
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def process_markdown_file(self, input_file: str) -> None:
        """处理markdown文件，生成优化后的笔记
        
        Args:
            input_file (str): 输入的markdown文件路径
        """
        try:
            # 读取markdown文件
            with open(input_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 提取视频链接
            video_links = re.findall(r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|bilibili\.com/video/|douyin\.com/video/)[^\s\)]+', content)
            
            if not video_links:
                print("未在markdown文件中找到视频链接")
                return
                
            print(f"找到 {len(video_links)} 个视频链接，开始处理...\n")
            
            # 处理每个视频链接
            for i, url in enumerate(video_links, 1):
                print(f"处理第 {i}/{len(video_links)} 个视频: {url}\n")
                self.process_video(url)
                
        except Exception as e:
            print(f"处理markdown文件时出错: {str(e)}")
            raise

    def generate_xhs_note_from_audio(self, url: str) -> dict:
        """
        输入音频url，直接返回小红书文案的markdown字符串、原文案transcript和整理文本organized_content
        """
        
        try:
            # 构造 video_info
            video_info = {
                'title': '音频转小红书',
                'uploader': '未知',
                'description': '',
                'duration': 0,
                'platform': 'douyin'
            }
            # 后续处理同 generate_xhs_note_from_url
            transcript = self._transcribe_audio(url)
            if not transcript:
                return {"error": "音频转录失败"}
            organized_content = self._organize_long_content(transcript, int(video_info['duration']))
            xhs_content, titles, tags, images = self.convert_to_xiaohongshu(organized_content)

            md = ""
            if titles:
                md += f"# {titles[0]}\n\n"
            else:
                md += "# 音频转小红书\n\n"
            if images:
                md += f"![封面图]({images[0]})\n\n"
            content_parts = xhs_content.split('\n\n')
            mid_point = len(content_parts) // 2
            md += '\n\n'.join(content_parts[:mid_point]) + '\n\n'
            if len(images) > 1:
                md += f"![配图]({images[1]})\n\n"
            md += '\n\n'.join(content_parts[mid_point:])
            if len(images) > 2:
                md += f"\n\n![配图]({images[2]})"
            if tags:
                md += "\n\n---\n"
                md += "\n".join([f"#{tag}" for tag in tags])
            return {"note": md, "transcript": transcript, "organized_content": organized_content}

        finally:
            print(f"转换完成")

    def generate_wj_note_from_audio(self, url: str) -> dict:
        """
        输入音频url，直接返回原文案transcript和违禁词整理文本organized_content
        """
        transcript = self._transcribe_audio(url)
        if not transcript:
            return {"error": "音频转录失败"}

        checked_content = self._check_long_content(transcript)
        return {"transcript": transcript, "checked_content": checked_content}

def extract_urls_from_text(text: str) -> list:
    """
    从文本中提取所有有效的URL
    支持的URL格式：
    - 视频平台URL (YouTube, Bilibili, 抖音等)
    - 包含http://或https://的标准URL
    - 短链接URL (如t.co等)
    
    Args:
        text: 输入文本
        
    Returns:
        list: 提取到的有效URL列表
    """
    # URL正则模式
    url_patterns = [
        # 标准URL
        r'https?://[^\s<>\[\]"\']+[^\s<>\[\]"\'.,]',
        # 短链接
        r'https?://[a-zA-Z0-9]+\.[a-zA-Z]{2,3}/[^\s<>\[\]"\']+',
        # Bilibili
        r'BV[a-zA-Z0-9]{10}',
        # 抖音分享链接
        r'v\.douyin\.com/[a-zA-Z0-9]+',
    ]
    
    urls = []
    for pattern in url_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            url = match.group()
            # 对于不完整的BV号，添加完整的bilibili前缀
            if url.startswith('BV'):
                url = f'https://www.bilibili.com/video/{url}'
            urls.append(url)
    
    # 去重并保持顺序
    seen = set()
    return [url for url in urls if not (url in seen or seen.add(url))]

if __name__ == '__main__':
    import sys, os, re
    import argparse
    
    parser = argparse.ArgumentParser(description='视频笔记生成器')
    parser.add_argument('input', help='输入源：视频URL、包含URL的文件或markdown文件')
    parser.add_argument('--xiaohongshu', action='store_true', help='生成小红书风格的笔记')
    args = parser.parse_args()
    
    generator = VideoNoteGenerator()
    
    if os.path.exists(args.input):
        # 读取文件内容
        try:
            with open(args.input, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                # 尝试使用gbk编码
                with open(args.input, 'r', encoding='gbk') as f:
                    content = f.read()
            except Exception as e:
                print(f"⚠️ 无法读取文件: {str(e)}")
                sys.exit(1)
        
        # 如果是markdown文件，直接处理
        if args.input.endswith('.md'):
            print(f"📝 处理Markdown文件: {args.input}")
            generator.process_markdown_file(args.input)
        else:
            # 从文件内容中提取URL
            urls = extract_urls_from_text(content)
            
            if not urls:
                print("⚠️ 未在文件中找到有效的URL")
                sys.exit(1)
            
            print(f"📋 从文件中找到 {len(urls)} 个URL:")
            for i, url in enumerate(urls, 1):
                print(f"  {i}. {url}")
            
            print("\n开始处理URL...")
            for i, url in enumerate(urls, 1):
                print(f"\n处理第 {i}/{len(urls)} 个URL: {url}")
                try:
                    generator.process_video(url)
                except Exception as e:
                    print(f"⚠️ 处理URL时出错：{str(e)}")
                    continue
    else:
        # 检查是否是有效的URL
        if not args.input.startswith(('http://', 'https://')):
            print("⚠️ 错误：请输入有效的URL、包含URL的文件或markdown文件路径")
            print("\n使用示例：")
            print("1. 处理单个视频：")
            print("   python video_note_generator.py https://example.com/video")
            print("\n2. 处理包含URL的文件：")
            print("   python video_note_generator.py urls.txt")
            print("   - 文件中的URL可以是任意格式，每行一个或多个")
            print("   - 支持带有其他文字的行")
            print("   - 支持使用#注释")
            print("\n3. 处理Markdown文件：")
            print("   python video_note_generator.py notes.md")
            sys.exit(1)
        
        # 处理单个URL
        try:
            print(f"🎥 处理视频URL: {args.input}")
            generator.process_video(args.input)
        except Exception as e:
            print(f"⚠️ 处理URL时出错：{str(e)}")
            sys.exit(1)

def recognize_audio_from_url(audio_url, secret_id, secret_key, region="ap-shanghai"):
    """
    使用腾讯云ASR的CreateRecTask API识别录音文件（通过URL方式）。

    Args:
        audio_url (str): 音频文件的公共可访问URL。
        secret_id (str): 您的腾讯云SecretId。
        secret_key (str): 您的腾讯云SecretKey。
        region (str): 腾讯云服务区域，默认为“ap-shanghai”。
    """
    try:
        # 实例化一个认证对象，入参需要传入腾讯云账户的 SecretId 和 SecretKey
        cred = credential.Credential(secret_id, secret_key)

        # 实例化一个http选项，可选的，没有特殊需求可以跳过
        httpProfile = HttpProfile()
        httpProfile.endpoint = "asr.tencentcloudapi.com" # ASR服务的域名

        # 实例化一个客户端配置对象，可选的，没有特殊需求可以跳过
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile

        # 实例化要请求产品的client对象
        client = asr_client.AsrClient(cred, region, clientProfile)

        # 实例化一个请求对象，根据API文档，此对象是CreateRecTaskRequest
        req = models.CreateRecTaskRequest()

        # 设置请求参数
        req.EngineModelType = "16k_zh_large"  # 指定引擎模型类型 [1]
        req.SourceType = 0                    # 音频来源：0表示音频URL [2]
        req.ChannelNum = 1                    # 声道数：1表示单声道 [2]
        req.ResTextFormat = 2                # 返回识别结果的格式 [2]
        req.Url = audio_url                   # 音频文件的URL [2]

        print(f"正在提交录音文件识别任务，URL: {audio_url}...")
        resp = client.CreateRecTask(req)
        task_id = resp.Data.TaskId
        print(f"任务提交成功，TaskId: {task_id}")

        # 轮询任务状态，直到识别完成 [3]
        print("正在等待识别结果...")
        while True:
            describe_req = models.DescribeTaskStatusRequest()
            describe_req.TaskId = task_id
            describe_resp = client.DescribeTaskStatus(describe_req)

            status_str = describe_resp.Data.StatusStr
            if status_str == "success":
                print("\n识别完成！")
                print("原始识别结果:")
                print(f"\n错误信息: {describe_resp.Data}")
                # 如果需要，可以在这里进一步处理 Result 字段，例如提取文本或生成SRT [3]
                return describe_resp.Data.Result
            elif status_str in ["failed", "error"]:
                print(f"\n识别失败，状态: {status_str}, 错误信息: {describe_resp.Data.ErrorMsg}")
                break
            else:
                print(f"当前任务状态: {status_str}，继续等待...")
                time.sleep(5) # 每5秒轮询一次 [3]

    except TencentCloudSDKException as err:
        print(f"腾讯云SDK异常: {err}")
    except Exception as e:
        print(f"发生未知错误: {e}")