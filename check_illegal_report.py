import os
import sys
import json
import time
import subprocess
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv
import openai

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.ocr.v20181119 import ocr_client, models

# 加载环境变量
load_dotenv()

# 检查必要的环境变量
required_env_vars = {
    'OPENROUTER_API_KEY': '用于OpenRouter API',
    'OPENROUTER_API_URL': '用于OpenRouter API',
    'OPENROUTER_APP_NAME': '用于OpenRouter API',
    'OPENROUTER_HTTP_REFERER': '用于OpenRouter API',
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

class CheckIllegalReport:
    def __init__(self, output_dir: str = "temp_pics"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        self.openrouter_available = openrouter_available
        

    def _determine_platform(self, url: str) -> Optional[str]:
        """
        确定平台
        
        Args:
            url: 详情URL
            
        Returns:
            str: 平台名称 ('tb', 'tm', 'jd') 或 None
        """
        if 'taobao.com' in url:
            return 'tb'
        elif 'jd.com' in url:
            return 'jd'
        elif 'tmall.com' in url:
            return 'tm'
        return None


    def _transcribe_image(self, image_path: str) -> str:
        """OCR识别文案"""
        try:              
            SECRET_ID = os.getenv("SECRET_ID")
            SECRET_KEY = os.getenv("SECRET_KEY")

            result = recognize_text_from_image(image_path, SECRET_ID, SECRET_KEY)
            return result if result is not None else ""
            
        except Exception as e:
            print(f"⚠️ 识别文案失败: {str(e)}")
            return ""

    def _check_content(self, content: str) -> str:
        """使用AI检查内容"""
        try:
            if not self.openrouter_available:
                print("⚠️ OpenRouter API 未配置，将返回原始内容")
                return content

            # 构建系统提示词
            system_prompt = """请你扮演一名经验丰富、极其严谨的电商内容审核专家，同时具备资深电商行业《广告法》合规顾问和过往违法案例分析师的专业视角。你的核心任务是，在深刻理解相关法规和历史违规案例的基础上，对电商广告文字进行全面、彻底的审查。

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
            final_prompt = f"""请根据以下电商广告文案内容，生成一份结构清晰、具有洞察力的违规检查报告。

电商广告文案内容：

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

    
    def generate_report_from_detail(self, url: str) -> dict:
        """
        输入详情url，直接返回图片文案
        """
        transcript = self._transcribe_image(url)
        if not transcript:
            return {"error": "图片识别失败"}

        checked_content = self._check_content(transcript)
        return {"transcript": transcript, "checked_content": checked_content}

def recognize_text_from_image(image_url, secret_id, secret_key, region="ap-shanghai"):
    """
    使用腾讯云OCR的API识别文字。
    """
    try:
        cred = credential.Credential(
            secret_id,
            secret_key)
        httpProfile = HttpProfile()
        httpProfile.endpoint = "ocr.tencentcloudapi.com"

        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = ocr_client.OcrClient(cred, region, clientProfile)

        req = models.GeneralFastOCRRequest()
        req.ImageUrl = image_url
        resp = client.GeneralFastOCR(req)
        return resp.to_json_string()

    except TencentCloudSDKException as err:
        print(err)