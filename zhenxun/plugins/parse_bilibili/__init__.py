import re
import time

import ujson as json
from nonebot import on_message
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import Hyper, UniMsg
from nonebot_plugin_saa import Image, MessageFactory, Text
from nonebot_plugin_session import EventSession

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.configs.utils import PluginExtraData, RegisterConfig, Task
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .information_container import InformationContainer
from .parse_url import parse_bili_url

__plugin_meta__ = PluginMetadata(
    name="B站转发解析",
    description="B站转发解析",
    usage="""
    usage：
        B站转发解析，解析b站分享信息，支持bv，bilibili链接，b站手机端转发卡片，cv，b23.tv，且30秒内内不解析相同url
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        menu_type="其他",
        configs=[
            RegisterConfig(
                module="_task",
                key="DEFAULT_BILIBILI_PARSE",
                value=True,
                default_value=True,
                help="被动 B站转发解析 进群默认开关状态",
                type=bool,
            )
        ],
        tasks=[Task(module="bilibili_parse", name="b站转发解析")],
    ).dict(),
)


async def _rule(session: EventSession) -> bool:
    task = await TaskInfo.get_or_none(module="bilibili_parse")
    if not task or not task.status:
        logger.debug("b站转发解析被动全局关闭，已跳过...")
        return False
    if gid := session.id3 or session.id2:
        return not await GroupConsole.is_block_task(gid, "bilibili_parse")
    return False


_matcher = on_message(priority=1, block=False, rule=_rule)

_tmp = {}


@_matcher.handle()
async def _(session: EventSession, message: UniMsg):
    information_container = InformationContainer()
    # 判断文本消息内容是否相关
    match = None
    # 判断文本消息和小程序的内容是否指向一个b站链接
    get_url = None
    # 判断文本消息是否包含视频相关内容
    vd_flag = False
    # 设定时间阈值，阈值之下不会解析重复内容
    repet_second = 300
    # 尝试解析小程序消息
    data = message[0]
    if isinstance(data, Hyper) and data.raw:
        try:
            data = json.loads(data.raw)
        except (IndexError, KeyError):
            data = None
        if data:
            # 获取相关数据
            meta_data = data.get("meta", {})
            news_value = meta_data.get("news", {})
            detail_1_value = meta_data.get("detail_1", {})
            qqdocurl_value = detail_1_value.get("qqdocurl", {})
            jumpUrl_value = news_value.get("jumpUrl", {})
            get_url = (qqdocurl_value if qqdocurl_value else jumpUrl_value).split("?")[
                0
            ]
    # 解析文本消息
    elif msg := message.extract_plain_text():
        # 消息中含有视频号
        if "bv" in msg.lower() or "av" in msg.lower():
            match = re.search(r"((?=(?:bv|av))([A-Za-z0-9]+))", msg, re.IGNORECASE)
            vd_flag = True

        # 消息中含有b23的链接，包括视频、专栏、动态、直播
        elif "https://b23.tv" in msg:
            match = re.search(r"https://b23\.tv/[^?\s]+", msg, re.IGNORECASE)

        # 检查消息中是否含有直播、专栏、动态链接
        elif any(
            keyword in msg
            for keyword in [
                "https://live.bilibili.com/",
                "https://www.bilibili.com/read/",
                "https://www.bilibili.com/opus/",
                "https://t.bilibili.com/",
            ]
        ):
            pattern = r"https://(live|www\.bilibili\.com/read|www\.bilibili\.com/opus|t\.bilibili\.com)/[^?\s]+"
            match = re.search(pattern, msg)

    # 匹配成功，则获取链接
    if match:
        if vd_flag:
            number = match.group(1)
            get_url = f"https://www.bilibili.com/video/{number}"
        else:
            get_url = match.group()

    if get_url:
        # 将链接统一发送给处理函数
        vd_info, live_info, vd_url, live_url, image_info, image_url = (
            await parse_bili_url(get_url, information_container)
        )
        if vd_info:
            # 判断一定时间内是否解析重复内容，或者是第一次解析
            if (
                vd_url in _tmp.keys() and time.time() - _tmp[vd_url] > repet_second
            ) or vd_url not in _tmp.keys():
                pic = vd_info.get("pic", "")  # 封面
                aid = vd_info.get("aid", "")  # av号
                title = vd_info.get("title", "")  # 标题
                author = vd_info.get("owner", {}).get("name", "")  # UP主
                reply = vd_info.get("stat", {}).get("reply", "")  # 回复
                favorite = vd_info.get("stat", {}).get("favorite", "")  # 收藏
                coin = vd_info.get("stat", {}).get("coin", "")  # 投币
                like = vd_info.get("stat", {}).get("like", "")  # 点赞
                danmuku = vd_info.get("stat", {}).get("danmaku", "")  # 弹幕
                ctime = vd_info["ctime"]
                date = time.strftime("%Y-%m-%d", time.localtime(ctime))
                logger.info(f"解析bilibili转发 {vd_url}", "b站解析", session=session)
                _tmp[vd_url] = time.time()
                _path = TEMP_PATH / f"{aid}.jpg"
                await AsyncHttpx.download_file(pic, _path)
                await MessageFactory(
                    [
                        Image(_path),
                        Text(
                            f"av{aid}\n标题：{title}\nUP：{author}\n上传日期：{date}\n回复：{reply}，收藏：{favorite}，投币：{coin}\n点赞：{like}，弹幕：{danmuku}\n{vd_url}"
                        ),
                    ]
                ).send()

        elif live_info:
            if (
                live_url in _tmp.keys() and time.time() - _tmp[live_url] > repet_second
            ) or live_url not in _tmp.keys():
                uid = live_info.get("uid", "")  # 主播uid
                title = live_info.get("title", "")  # 直播间标题
                description = live_info.get("description", "")  # 简介，可能会出现标签
                user_cover = live_info.get("user_cover", "")  # 封面
                keyframe = live_info.get("keyframe", "")  # 关键帧画面
                live_time = live_info.get("live_time", "")  # 开播时间
                area_name = live_info.get("area_name", "")  # 分区
                parent_area_name = live_info.get("parent_area_name", "")  # 父分区
                logger.info(f"解析bilibili转发 {live_url}", "b站解析", session=session)
                _tmp[live_url] = time.time()
                await MessageFactory(
                    [
                        Image(user_cover),
                        Text(
                            f"开播用户：https://space.bilibili.com/{uid}\n开播时间：{live_time}\n直播分区：{parent_area_name}——>{area_name}\n标题：{title}\n简介：{description}\n直播截图：\n"
                        ),
                        Image(keyframe),
                        Text(f"{live_url}"),
                    ]
                ).send()
        elif image_info:
            if (
                image_url in _tmp.keys()
                and time.time() - _tmp[image_url] > repet_second
            ) or image_url not in _tmp.keys():
                logger.info(f"解析bilibili转发 {image_url}", "b站解析", session=session)
                _tmp[image_url] = time.time()
                await image_info.send()
