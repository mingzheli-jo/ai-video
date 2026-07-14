from __future__ import annotations

import base64
import binascii
import json
import math
import os
import shutil
import subprocess
import sys
import uuid
import wave
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

from video_factory.fonts import load_font


@dataclass(frozen=True)
class VideoConfig:
    topic: str = "创作者变现：流量分账收入公开"
    target_duration: int = 45
    style: str = "ai_realistic_montage"
    goal: str = "retention_growth"
    reference_title: str = "十万粉收入公开！教程：如何快速获得流量分账收入"
    output_slug: str = "release"
    tts_provider: str = "openai"
    tts_model: str = "gpt-4o-mini-tts"
    voice: str = "marin"
    voice_instructions: str = "用中文短视频教程口吻，语速偏快但清楚，像经验型创作者在直接分享方法。"


@dataclass(frozen=True)
class TTSConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini-tts"
    voice: str = "marin"
    voice_instructions: str = "用中文短视频教程口吻，语速偏快但清楚，像经验型创作者在直接分享方法。"
    audio_file: Path | None = None
    allow_fallback: bool = False
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: int = 120
    edge_rate: str = "+20%"
    doubao_appid_env: str = "VOLC_TTS_APPID"
    doubao_token_env: str = "VOLC_TTS_TOKEN"
    doubao_cluster: str = "volcano_tts"
    # 新版豆包语音控制台（快捷API接入）只发一个 API Key；设了它就走 v3 接口，优先于 appid+token。
    doubao_apikey_env: str = "VOLC_TTS_APIKEY"


@dataclass(frozen=True)
class TTSResult:
    path: Path
    provider: str
    voice: str
    model: str
    used_fallback: bool
    notes: str


class TTSProviderError(Exception):
    """Raised when the configured TTS provider cannot produce release audio."""


@dataclass(frozen=True)
class Segment:
    role: str
    start: int
    end: int
    screen_text: str
    narration: str
    visual_prompt: str
    english_subtitle: str = ""
    panel_type: str = "creator_dashboard"
    key_points: Sequence[str] = ()

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class VideoPlan:
    config: VideoConfig
    width: int
    height: int
    fps: int
    segments: List[Segment]


@dataclass(frozen=True)
class RenderResult:
    script: Path
    storyboard: Path
    prompts: Path
    subtitles: Path
    voiceover: Path
    concat: Path
    video: Path
    frames: List[Path]
    cover: Path
    report: Path


def build_video_plan(config: VideoConfig) -> VideoPlan:
    if config.target_duration != 45:
        raise ValueError("The v1 template is fixed at 45 seconds.")

    visual_style = (
        "AI realistic montage, vertical Douyin tutorial, synthetic presenter, "
        "creator dashboard overlays, clean commercial lighting, no copied Douyin footage"
    )
    segments = [
        Segment(
            role="hook",
            start=0,
            end=3,
            screen_text="10万粉账号\n到底能拿多少分账？",
            narration="十万粉账号，真正靠流量分账能拿多少钱？先别急着羡慕，我把路径拆给你看。",
            visual_prompt=(
                f"{visual_style}; confident Chinese creator holding a phone; "
                "large revenue number overlay; urgent opening hook."
            ),
        ),
        Segment(
            role="counterintuitive_truth",
            start=3,
            end=10,
            screen_text="不是粉丝多就赚钱\n而是内容能被看完",
            narration="很多人以为粉丝越多越赚钱，其实平台先看完播、互动和内容垂直度。",
            visual_prompt=(
                f"{visual_style}; split screen with follower count crossed out and retention chart highlighted; "
                "teacher-like creator expression."
            ),
        ),
        Segment(
            role="step_1",
            start=10,
            end=18,
            screen_text="第一步\n只做一个垂直问题",
            narration="第一步，只围绕一个高频问题做系列，比如新手怎么开通分账，怎么选题，怎么提高完播。",
            visual_prompt=(
                f"{visual_style}; creator in home studio pointing at three sticky-note topics; "
                "phone screen shows niche content calendar."
            ),
        ),
        Segment(
            role="step_2",
            start=18,
            end=26,
            screen_text="第二步\n开头3秒给结果",
            narration="第二步，前三秒直接给结果。不要铺垫，先说收益、数据、结论，再解释过程。",
            visual_prompt=(
                f"{visual_style}; close-up of video timeline, first three seconds marked in red; "
                "bold result-first caption."
            ),
        ),
        Segment(
            role="step_3",
            start=26,
            end=32,
            screen_text="第三步\n用案例证明你真的做过",
            narration="第三步，用截图感数据和案例证明，不空讲道理。观众相信你，才会继续看。",
            visual_prompt=(
                f"{visual_style}; anonymized analytics dashboard, comment bubbles, and creator review notes; "
                "no real platform private data."
            ),
        ),
        Segment(
            role="summary",
            start=32,
            end=42,
            screen_text="公式\n结果钩子 + 三步教程 + 数据证明",
            narration="记住这个公式：结果钩子，加三步教程，再加数据证明。每天复用一个结构，先把完播率跑起来。",
            visual_prompt=(
                f"{visual_style}; clean whiteboard formula, arrows from hook to tutorial to proof; "
                "creator stands beside monetization workflow."
            ),
        ),
        Segment(
            role="follow",
            start=42,
            end=45,
            screen_text="想要选题模板\n评论：分账",
            narration="想要这套选题模板，关注我，评论分账。",
            visual_prompt=(
                f"{visual_style}; creator gestures to follow button and comment keyword; "
                "bright final call-to-action frame."
            ),
        ),
    ]
    return VideoPlan(config=config, width=1080, height=1920, fps=30, segments=segments)


def build_portugal_dr_congo_prediction_plan(config: VideoConfig | None = None) -> VideoPlan:
    base_config = config or VideoConfig(
        topic="Portugal vs DR Congo：AI Skill 世界杯赛前预测",
        target_duration=340,
        style="premium_studio_tutorial",
        goal="sports_prediction_retention",
        reference_title="C罗第六届世界杯首战，AI预测葡萄牙会不会翻车",
        output_slug="portugal-dr-congo-release",
        tts_provider="edge",
        voice="zh-CN-YunxiNeural",
        voice_instructions="用中文体育解说口吻，节奏稳，重点数字和比分要咬字清楚，像在做赛前战术分析。",
    )
    if base_config.target_duration != 340:
        raise ValueError("The Portugal vs DR Congo long-form template is fixed at 340 seconds.")

    visual_style = (
        "dark sports-tech studio, AI silhouette presenter, neon green prediction dashboard, "
        "Portugal vs DR Congo World Cup 2026 analysis, bilingual subtitles, original UI only"
    )
    segments = [
        Segment(
            role="hook",
            start=0,
            end=25,
            screen_text="AI预测\n葡萄牙 2:1 DR Congo",
            narration=(
                "这场葡萄牙对刚果民主共和国，表面看是强弱局，但我的AI Skill给出的结论不是大胜，"
                "而是葡萄牙二比一小胜。为什么不是三比零，甚至不是一场完全没有悬念的比赛？"
                "因为模型看到的不是球星名气，而是比赛走势。葡萄牙当然更强，可首战最怕的就是节奏没打开、"
                "机会没转化、后场还被对手用速度冲一次。真正危险的地方，不在九十分钟平均实力，而在前三十分钟。"
                "所以这条视频先给结论，再拆原因。"
            ),
            english_subtitle="This is not a simple mismatch. The model projects Portugal 2-1 DR Congo.",
            panel_type="score_prediction",
            key_points=("Portugal 2:1 DR Congo", "not a guaranteed result", "early goal sensitivity"),
            visual_prompt=f"{visual_style}; opening score card, large 2-1 projection, urgent hook.",
        ),
        Segment(
            role="match_setup",
            start=25,
            end=80,
            screen_text="Group K首战\n北京时间 6月18日 01:00",
            narration=(
                "先把背景摆清楚。这是二零二六世界杯K组的一场焦点战，休斯敦当地六月十七日中午开球，"
                "换到北京时间大约是六月十八日凌晨一点。葡萄牙这边最大的公共叙事，当然是C罗第六届世界杯，"
                "这个话题天然会把注意力吸走。但如果只看C罗，这场预测就会变得很粗糙。世界杯小组赛第一轮，"
                "强队经常不是输在能力，而是输在开局判断：是先稳住控球，还是立刻把边路压上去？"
                "是让老将承担终结，还是让中场和边锋先把节奏跑起来？刚果民主共和国重回世界杯舞台，"
                "心理能量会很足，他们不会主动把比赛交出去。所以这场的本质，是葡萄牙的控制力，"
                "对上刚果民主共和国的冲击力。这里的预测不能只问谁更有名，而要问谁能先把比赛带进自己的节奏。"
            ),
            english_subtitle="Group K opener in Houston, with Beijing time around 01:00 on June 18.",
            panel_type="fixture_card",
            key_points=("Group K", "June 17 local time", "June 18 Beijing time", "Ronaldo storyline"),
            visual_prompt=f"{visual_style}; fixture card, Houston stadium map, Group K label.",
        ),
        Segment(
            role="portugal_advantage",
            start=80,
            end=155,
            screen_text="葡萄牙优势\n不只是C罗",
            narration=(
                "模型把葡萄牙放在优势方，核心原因不是情怀，而是阵容厚度。"
                "他们有更多能制造最后一传的人，有更稳定的中场控球，也有边路把防线拉开的能力。"
                "这类比赛里，葡萄牙最理想的剧本不是一上来就打得很急，而是通过中场连续传导，"
                "把刚果民主共和国的第一道压迫拉散。只要肋部出现空当，葡萄牙就能用斜传、倒三角、"
                "二点球继续制造压力。C罗的意义在这里不是全场都要持球，而是他会改变对方中卫的站位，"
                "让防线不敢轻易前顶。真正决定胜率的，是他身后的创造力和二次进攻。"
                "如果葡萄牙早早进球，比赛会被拖进他们最舒服的控球节奏，刚果民主共和国必须往外压，"
                "身后空间就会变大。模型在这个分支里给葡萄牙的胜率最高，因为比赛会从对抗题变成效率题。"
                "换句话说，葡萄牙不是一定要靠单点爆破赢球，他们可以靠连续压迫、二次落点和替补深度慢慢磨出机会。"
            ),
            english_subtitle="Portugal are favored because of depth, control, chance creation, and width.",
            panel_type="advantage_bars",
            key_points=("squad depth", "midfield control", "chance creation", "wide overloads"),
            visual_prompt=f"{visual_style}; Portugal advantage bars, player-card wall, control map.",
        ),
        Segment(
            role="dr_congo_risk",
            start=155,
            end=225,
            screen_text="爆冷窗口\n反击和定位球",
            narration=(
                "但刚果民主共和国不是来当背景板的。模型给他们的窗口，主要在身体对抗、直接反击和定位球。"
                "这种球队打强队，最怕的不是控球率低，而是抢下来之后第一脚出不去；只要第一脚能打到前场，"
                "比赛立刻会变成冲刺局。葡萄牙如果两个边后卫同时压上，中场保护又慢半拍，"
                "刚果民主共和国就能把球打到边路或者中卫身后，逼葡萄牙回追。另一个变量是定位球，"
                "世界杯首战的强队往往会紧，禁区里一次盯人失误，就可能把整个预测模型打乱。"
                "所以我不把这场写成葡萄牙轻松碾压。刚果民主共和国的赢面不高，但他们有办法让比赛变脏、"
                "变乱、变成葡萄牙不想踢的节奏。尤其是比分还在零比零的时候，弱势方每一次成功防守都会变成情绪奖励，"
                "观众声浪、身体接触和裁判尺度都会影响强队的耐心。葡萄牙如果开始强行传中，反而会把比赛送进对手喜欢的区域。"
            ),
            english_subtitle="DR Congo's upset window is physical duels, counters, and set pieces.",
            panel_type="risk_map",
            key_points=("physical duels", "direct counters", "set pieces", "Portugal transition defense"),
            visual_prompt=f"{visual_style}; yellow warning board, counterattack route lines, upset meter.",
        ),
        Segment(
            role="ai_skill_simulation",
            start=225,
            end=295,
            screen_text="AI Skill推演\n胜率不是稳胆",
            narration=(
                "我的Skill会把这场拆成几个变量：进攻质量、中场控制、防守转换、定位球风险、"
                "赛地和开局压力。第一层看硬实力，葡萄牙明显占优；第二层看比赛节奏，葡萄牙也更容易控住球。"
                "但第三层是风险层，问题就出来了：如果葡萄牙压上之后丢球，防线转身速度和中场回收距离都会被放大。"
                "所以模型会跑三种剧本。第一种，葡萄牙三十分钟内进球，后面胜率明显抬高，比分可能走到二比零或者二比一。"
                "第二种，零比零拖到半场，刚果民主共和国的信心会上来，葡萄牙的射门选择会变急，冷门概率上升。"
                "第三种，也是最危险的分支，如果刚果民主共和国先进球，葡萄牙会进入压力测试，"
                "场面可能好看，但结果会非常难受。综合三种分支，模型仍然给葡萄牙胜，但把大胜概率压低。"
                "这就是二比一这个比分的来源：优势在葡萄牙，警报也一直在葡萄牙身后。"
            ),
            english_subtitle="The Skill separates attack, control, transition defense, set pieces, and venue context.",
            panel_type="simulation",
            key_points=("attack quality", "control score", "transition risk", "three scenarios"),
            visual_prompt=f"{visual_style}; code input box, probability table, scenario branches.",
        ),
        Segment(
            role="final_prediction",
            start=295,
            end=340,
            screen_text="最终预测\nPortugal 2:1 DR Congo",
            narration=(
                "所以最终预测，我会给葡萄牙二比一。葡萄牙赢面更大，但不是稳胆。"
                "我会重点看三个信号：第一，葡萄牙前十五分钟能不能把球推进到禁区两侧；"
                "第二，刚果民主共和国反击第一脚能不能越过中场；第三，定位球防守有没有明显错位。"
                "如果前两个信号都站在葡萄牙这边，二比一甚至二比零都合理；如果葡萄牙久攻不下，"
                "这场就会比纸面实力危险得多。最后再强调一次，这个结论是赛前模型推演，不是确定结果。"
                "真正的答案，还是要等比赛自己给。你可以把它当成观赛前的路线图：看进球时间，看反击质量，"
                "再看葡萄牙有没有被迫离开自己的节奏。这样看球，会更接近比赛本身。"
            ),
            english_subtitle="Final projection: Portugal 2-1 DR Congo. It is a model estimate, not a guarantee.",
            panel_type="final_score",
            key_points=("Portugal 2:1 DR Congo", "favored but volatile", "model estimate only"),
            visual_prompt=f"{visual_style}; final score panel, caution badge, closing presenter frame.",
        ),
    ]
    return VideoPlan(config=base_config, width=1920, height=1080, fps=30, segments=segments)


def write_artifacts(plan: VideoPlan, output_dir: Path | str) -> Dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    paths = {
        "script": output_path / "script.md",
        "storyboard": output_path / "storyboard.json",
        "prompts": output_path / "visual_prompts.md",
        "subtitles": output_path / "subtitles.srt",
    }
    paths["script"].write_text(_build_script_markdown(plan), encoding="utf-8")
    paths["storyboard"].write_text(_build_storyboard_json(plan), encoding="utf-8")
    paths["prompts"].write_text(_build_visual_prompts(plan), encoding="utf-8")
    paths["subtitles"].write_text(_build_srt(plan), encoding="utf-8")
    return paths


def load_visual_asset_manifest(path: Path | str) -> Dict[str, object]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_keys = {"version", "license_policy", "assets", "segment_asset_order"}
    missing = required_keys - set(payload)
    if missing:
        raise ValueError(f"Asset manifest is missing keys: {', '.join(sorted(missing))}")
    for asset in payload["assets"]:
        for key in ("key", "kind", "segment_role", "local_path", "source_url", "license_url", "notes"):
            if key not in asset:
                raise ValueError(f"Asset entry {asset.get('key', '<unknown>')} is missing {key}")
    return payload


def render_video(
    plan: VideoPlan,
    output_dir: Path | str,
    tts_config: TTSConfig | None = None,
    release: bool = False,
    visual_asset_manifest: Path | str | None = None,
) -> RenderResult:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    text_artifacts = write_artifacts(plan, output_path)
    frames: List[Path] = []
    if visual_asset_manifest is None:
        frames_dir = output_path / "frames"
        frames_dir.mkdir(exist_ok=True)
        frames = _render_frames(plan, frames_dir)

    voiceover_path = output_path / "voiceover.wav"
    voiceover_result = synthesize_voiceover(
        plan,
        voiceover_path,
        tts_config or TTSConfig(
            provider=plan.config.tts_provider,
            model=plan.config.tts_model,
            voice=plan.config.voice,
            voice_instructions=plan.config.voice_instructions,
        ),
    )

    if visual_asset_manifest is not None:
        manifest_path = Path(visual_asset_manifest)
        manifest = load_visual_asset_manifest(manifest_path)
        segment_videos = _render_premium_asset_segments(plan, output_path, manifest)
        video_path = output_path / ("release.mp4" if release else f"{plan.config.output_slug}.mp4")
        _concat_segment_videos_with_audio(
            segment_videos,
            voiceover_path,
            video_path,
            plan.config.target_duration,
        )
        concat_path = video_path.with_name("segments.txt")
        cover_path = output_path / "cover.png"
        _extract_cover_frame(segment_videos[0], cover_path)
        report_path = output_path / "render_report.json"
        _write_render_report(
            plan,
            voiceover_result,
            video_path,
            cover_path,
            report_path,
            segment_videos,
            render_mode="premium_asset_edit",
            asset_manifest=manifest_path,
        )
        return RenderResult(
            script=text_artifacts["script"],
            storyboard=text_artifacts["storyboard"],
            prompts=text_artifacts["prompts"],
            subtitles=text_artifacts["subtitles"],
            voiceover=voiceover_path,
            concat=concat_path,
            video=video_path,
            frames=segment_videos,
            cover=cover_path,
            report=report_path,
        )

    concat_path = output_path / "frames.txt"
    _write_concat_file(plan, frames, concat_path)

    video_path = output_path / ("release.mp4" if release else f"{plan.config.output_slug}.mp4")
    command = build_ffmpeg_command(
        concat_path,
        voiceover_path,
        video_path,
        duration=plan.config.target_duration,
        fps=plan.fps,
        width=plan.width,
        height=plan.height,
    )
    subprocess.run(command, check=True)
    cover_path = output_path / "cover.png"
    shutil.copyfile(frames[0], cover_path)
    report_path = output_path / "render_report.json"
    _write_render_report(plan, voiceover_result, video_path, cover_path, report_path, frames)

    return RenderResult(
        script=text_artifacts["script"],
        storyboard=text_artifacts["storyboard"],
        prompts=text_artifacts["prompts"],
        subtitles=text_artifacts["subtitles"],
        voiceover=voiceover_path,
        concat=concat_path,
        video=video_path,
        frames=frames,
        cover=cover_path,
        report=report_path,
    )


def build_ffmpeg_command(
    frames_file: Path | str,
    audio_file: Path | str,
    output_file: Path | str,
    duration: int = 45,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
) -> List[str]:
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(frames_file),
        "-i",
        str(audio_file),
        "-t",
        str(duration),
        "-vf",
        f"scale={width}:{height}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-af",
        "apad",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_file),
    ]


def build_segment_video_command(
    background_path: Path | str,
    overlay_path: Path | str,
    output_path: Path | str,
    duration: int,
    width: int,
    height: int,
    fps: int,
    background_kind: str,
) -> List[str]:
    background = str(background_path)
    overlay = str(overlay_path)
    output = str(output_path)
    filter_graph = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,"
        "eq=contrast=1.08:brightness=-0.08:saturation=0.82,"
        "format=rgba[bg];"
        "[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
    )
    if background_kind == "video":
        inputs = ["-stream_loop", "-1", "-i", background, "-i", overlay]
    else:
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", background, "-i", overlay]
    return [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-an",
        "-t",
        str(duration),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        output,
    ]


def build_tone_track(plan: VideoPlan, output_path: Path | str) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    sample_rate = 44100
    total_samples = plan.config.target_duration * sample_rate
    segment_starts = {segment.start: 760 + index * 35 for index, segment in enumerate(plan.segments)}
    frames = bytearray()

    for sample_index in range(total_samples):
        t = sample_index / sample_rate
        value = 0.0

        beat_position = t % 1.0
        if beat_position < 0.035:
            envelope = 1.0 - beat_position / 0.035
            value += 0.10 * envelope * math.sin(2 * math.pi * 540 * beat_position)

        for start, frequency in segment_starts.items():
            offset = t - start
            if 0 <= offset < 0.22:
                envelope = 1.0 - offset / 0.22
                value += 0.24 * envelope * math.sin(2 * math.pi * frequency * offset)

        int_sample = int(max(-1.0, min(1.0, value)) * 32767)
        frames.extend(int_sample.to_bytes(2, byteorder="little", signed=True))

    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)

    return output


def build_openai_speech_payload(source: "VideoPlan | str", tts_config: TTSConfig) -> Dict[str, str]:
    return {
        "model": tts_config.model,
        "voice": tts_config.voice,
        "input": _resolve_narration(source),
        "instructions": tts_config.voice_instructions,
        "response_format": "wav",
    }


DOUBAO_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
# v3 单向流式（HTTP chunked，每行一个 JSON 事件）：新版控制台 API Key 鉴权走这里。
DOUBAO_V3_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
# v3 资源 ID：语音合成1.0=seed-tts-1.0、2.0=seed-tts-2.0，可用环境变量覆盖。
DOUBAO_V3_RESOURCE_ID_ENV = "VOLC_TTS_RESOURCE_ID"
DOUBAO_V3_DEFAULT_RESOURCE_ID = "seed-tts-1.0"
DOUBAO_V3_URANUS_RESOURCE_ID = "seed-tts-2.0"
# v3 流式结束标志事件码（官方契约）。
DOUBAO_V3_DONE_CODE = 20000000

# TTSConfig.voice 的默认值 "marin" 是 openai 音色，对 edge/doubao 无意义（会被 API 拒）。
# 用户没显式指定音色（空或仍是 marin）时，各 provider 回落到自己的合理中文默认音色。
_CROSS_PROVIDER_DEFAULT_VOICE = "marin"
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DOUBAO_DEFAULT_VOICE = "zh_male_liufei_uranus_bigtts"


def _resolve_provider_voice(voice: str, provider_default: str) -> str:
    if not voice or voice == _CROSS_PROVIDER_DEFAULT_VOICE:
        return provider_default
    return voice


def _doubao_v3_resource_for_voice(voice: str) -> str:
    """豆包 v3 资源 ID 必须与音色族匹配，错配会报 55000000（resource ID mismatched with
    speaker）。uranus 系音色（如刘飞 zh_male_liufei_uranus_bigtts）要 seed-tts-2.0，
    moon 系（爽快思思等）用 seed-tts-1.0。VOLC_TTS_RESOURCE_ID 显式设置时以它为准
    （给未来新音色族留逃生阀）。"""
    override = (os.getenv(DOUBAO_V3_RESOURCE_ID_ENV) or "").strip()
    if override:
        return override
    if "uranus" in (voice or "").lower():
        return DOUBAO_V3_URANUS_RESOURCE_ID
    return DOUBAO_V3_DEFAULT_RESOURCE_ID


def build_doubao_speech_payload(source: "VideoPlan | str", tts_config: TTSConfig, appid: str, token: str) -> Dict:
    return {
        "app": {"appid": appid, "token": token, "cluster": tts_config.doubao_cluster},
        "user": {"uid": "video_factory"},
        "audio": {
            "voice_type": tts_config.voice,
            "encoding": "mp3",
            "speed_ratio": 1.0,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": _resolve_narration(source),
            "operation": "query",
        },
    }


def build_doubao_v3_payload(source: "VideoPlan | str", tts_config: TTSConfig) -> Dict:
    """v3 单向流式请求体（官方契约：user + req_params.text/speaker/audio_params）。"""
    return {
        "user": {"uid": "video_factory"},
        "req_params": {
            "text": _resolve_narration(source),
            "speaker": tts_config.voice,
            "audio_params": {"format": "mp3", "sample_rate": 24000},
        },
    }


def _resolve_narration(source: "VideoPlan | str") -> str:
    """兼容旧签名：payload 构造器既接受 VideoPlan 也接受口播文本字符串。"""
    if isinstance(source, str):
        return source
    return _narration_text(source)


def synthesize_voiceover(
    plan: VideoPlan,
    voiceover_path: Path | str,
    tts_config: TTSConfig | None = None,
) -> TTSResult:
    config = tts_config or TTSConfig(
        provider=plan.config.tts_provider,
        model=plan.config.tts_model,
        voice=plan.config.voice,
        voice_instructions=plan.config.voice_instructions,
    )
    output = Path(voiceover_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    provider = config.provider.lower()

    # tone 依赖 plan 的段结构生成节奏音轨，因此保留 plan 签名；其余 provider
    # 只吃口播文本，通过 fallback 回调把 plan 兜底到 tone 上（不改变现有对外行为）。
    narration = _narration_text(plan)
    on_fallback = lambda reason: _synthesize_tone_voiceover(plan, output, reason)

    if provider == "openai":
        return _synthesize_openai_voiceover(narration, output, config, on_fallback)
    if provider == "file":
        return _synthesize_file_voiceover(output, config)
    if provider == "edge":
        return _synthesize_edge_voiceover(narration, output, config, on_fallback)
    if provider == "doubao":
        return _synthesize_doubao_voiceover(narration, output, config, on_fallback)
    if provider == "tone":
        return _synthesize_tone_voiceover(plan, output, "Explicit tone provider selected.")
    raise TTSProviderError(f"Unsupported TTS provider: {config.provider}")


def synthesize_voiceover_text(
    narration: str,
    voiceover_path: Path | str,
    tts_config: TTSConfig | None = None,
) -> TTSResult:
    """文本级 TTS 入口：只吃口播文本，供拼装流水线在没有 VideoPlan 时使用。

    与 synthesize_voiceover 共用同一套 provider 分发。tone 依赖 plan 段结构，
    这里没有 plan，因此禁用 fallback：openai/doubao/edge 缺凭证或失败时直接抛
    TTSProviderError，tone provider 本身不受支持。
    """
    if not str(narration or "").strip():
        raise TTSProviderError("口播文案为空，无法合成配音。")
    config = tts_config or TTSConfig()
    output = Path(voiceover_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    provider = config.provider.lower()

    def _no_fallback(reason: str) -> TTSResult:
        raise TTSProviderError(
            f"文本级 TTS 不支持 fallback 兜底音轨（{reason}）。请补全对应服务商凭证或改用 --audio。"
        )

    if provider == "openai":
        return _synthesize_openai_voiceover(narration, output, config, _no_fallback)
    if provider == "file":
        return _synthesize_file_voiceover(output, config)
    if provider == "edge":
        return _synthesize_edge_voiceover(narration, output, config, _no_fallback)
    if provider == "doubao":
        return _synthesize_doubao_voiceover(narration, output, config, _no_fallback)
    if provider == "tone":
        raise TTSProviderError("文本级 TTS 不支持 tone 节奏音轨（依赖分段结构）。请选择 openai/doubao/edge。")
    raise TTSProviderError(f"Unsupported TTS provider: {config.provider}")


def wrap_video_text(text: str, limit: int) -> List[str]:
    punctuation = set("。，！？；：,.!?;:")
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        while len(line) > limit:
            chunk = line[:limit]
            line = line[limit:]
            if line and line[0] in punctuation:
                chunk += line[0]
                line = line[1:]
            lines.append(chunk)
        if line:
            if len(line) == 1 and line in punctuation and lines:
                lines[-1] += line
            else:
                lines.append(line)
    return lines or [text]


def _synthesize_openai_voiceover(
    narration: str,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
) -> TTSResult:
    api_key = os.getenv(config.api_key_env)
    if not api_key:
        if config.allow_fallback:
            return on_fallback(f"{config.api_key_env} is missing; fallback tone track generated.")
        raise TTSProviderError(
            f"{config.api_key_env} is required for OpenAI TTS. "
            "Pass --allow-fallback for a non-release rhythm guide track."
        )

    payload = build_openai_speech_payload(narration, config)
    request = Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            output.write_bytes(response.read())
    except HTTPError as exc:
        raise TTSProviderError(f"OpenAI TTS HTTP error: {exc.code}") from exc
    except URLError as exc:
        raise TTSProviderError(f"OpenAI TTS connection error: {exc.reason}") from exc

    if _probe_duration_seconds(output) <= 1:
        if config.allow_fallback:
            return on_fallback("OpenAI TTS returned unusable audio; fallback tone track generated.")
        raise TTSProviderError("OpenAI TTS returned unusable audio.")

    notes = "Voiceover generated with OpenAI Speech API.\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="openai",
        voice=config.voice,
        model=config.model,
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_doubao_voiceover(
    narration: str,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
) -> TTSResult:
    api_key = (os.getenv(config.doubao_apikey_env) or "").strip()
    appid = os.getenv(config.doubao_appid_env)
    token = os.getenv(config.doubao_token_env)
    if not api_key and (not appid or not token):
        if config.allow_fallback:
            return on_fallback(
                f"{config.doubao_apikey_env}/{config.doubao_appid_env} is missing; fallback tone track generated."
            )
        raise TTSProviderError(
            f"{config.doubao_apikey_env}（新版豆包语音控制台·快捷API接入的单个 API Key）"
            f"或 {config.doubao_appid_env}+{config.doubao_token_env}（旧版语音技术控制台）"
            "至少配置一种，才能使用 Doubao (Volcengine) TTS。"
            "Pass --allow-fallback for a non-release rhythm guide track."
        )

    config = replace(config, voice=_resolve_provider_voice(config.voice, DOUBAO_DEFAULT_VOICE))
    if api_key:
        mp3_bytes = _doubao_v3_fetch_audio(narration, config, api_key)
        model_label = "volcengine/v3-seed-tts"
    else:
        mp3_bytes = _doubao_v1_fetch_audio(narration, config, appid, token)
        model_label = f"volcengine/{config.doubao_cluster}"
    return _finalize_doubao_audio(mp3_bytes, output, config, on_fallback, model_label)


def _doubao_v1_fetch_audio(narration: str, config: TTSConfig, appid: str, token: str) -> bytes:
    """旧版 v1 接口（appid+token）：单次 JSON 响应，data 字段是整段 base64 音频。"""
    payload = build_doubao_speech_payload(narration, config, appid=appid, token=token)
    request = Request(
        DOUBAO_TTS_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            # 火山引擎 v1 鉴权格式是 "Bearer;<token>"，分号不是笔误。
            "Authorization": f"Bearer;{token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TTSProviderError(f"Doubao TTS HTTP error: {exc.code}") from exc
    except URLError as exc:
        raise TTSProviderError(f"Doubao TTS connection error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise TTSProviderError("Doubao TTS returned a non-JSON response.") from exc

    if body.get("code") != 3000 or not body.get("data"):
        raise TTSProviderError(
            f"Doubao TTS error {body.get('code')}: {body.get('message') or 'no audio data returned'}"
        )
    try:
        return base64.b64decode(body["data"])
    except (binascii.Error, ValueError) as exc:
        raise TTSProviderError("Doubao TTS returned malformed base64 audio.") from exc


def _doubao_v3_fetch_audio(narration: str, config: TTSConfig, api_key: str) -> bytes:
    """新版 v3 单向流式（API Key）：chunked 逐行 JSON 事件，data 是 base64 音频分片。"""
    payload = build_doubao_v3_payload(narration, config)
    resource_id = _doubao_v3_resource_for_voice(config.voice)
    request = Request(
        DOUBAO_V3_TTS_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise TTSProviderError(f"Doubao TTS(v3) HTTP error: {exc.code}") from exc
    except URLError as exc:
        raise TTSProviderError(f"Doubao TTS(v3) connection error: {exc.reason}") from exc

    chunks: list[bytes] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TTSProviderError("Doubao TTS(v3) returned a non-JSON stream line.") from exc
        code = event.get("code")
        if code == DOUBAO_V3_DONE_CODE:
            break
        if code != 0:
            raise TTSProviderError(
                f"Doubao TTS(v3) error {code}: {event.get('message') or 'unknown'}"
            )
        data = event.get("data")
        if data:
            try:
                chunks.append(base64.b64decode(data))
            except (binascii.Error, ValueError) as exc:
                raise TTSProviderError("Doubao TTS(v3) returned malformed base64 audio.") from exc
    if not chunks:
        raise TTSProviderError("Doubao TTS(v3) returned no audio data.")
    return b"".join(chunks)


def _finalize_doubao_audio(
    mp3_bytes: bytes,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
    model_label: str,
) -> TTSResult:
    temp_media = output.with_suffix(".doubao.mp3")
    temp_media.write_bytes(mp3_bytes)
    if _probe_duration_seconds(temp_media) <= 1:
        temp_media.unlink(missing_ok=True)
        if config.allow_fallback:
            return on_fallback("Doubao TTS returned unusable audio; fallback tone track generated.")
        raise TTSProviderError("Doubao TTS returned unusable audio.")

    try:
        _convert_audio(temp_media, output)
    except subprocess.CalledProcessError as exc:
        raise TTSProviderError("Failed to convert Doubao TTS audio to WAV.") from exc
    finally:
        temp_media.unlink(missing_ok=True)
    if _probe_duration_seconds(output) <= 1:
        raise TTSProviderError("Converted Doubao TTS audio is not usable.")
    notes = f"Voiceover generated with Doubao (Volcengine) TTS voice {config.voice}.\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="doubao",
        voice=config.voice,
        model=model_label,
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_file_voiceover(output: Path, config: TTSConfig) -> TTSResult:
    if not config.audio_file:
        raise TTSProviderError("A local audio file is required for the file TTS provider.")
    source = Path(config.audio_file)
    if not source.exists():
        raise TTSProviderError(f"The configured audio file does not exist: {source}")
    _convert_audio(source, output)
    if _probe_duration_seconds(output) <= 1:
        raise TTSProviderError(f"The configured audio file is not usable: {source}")
    notes = f"Voiceover copied from local audio file: {source}\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="file",
        voice=str(source),
        model="local-file",
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_edge_voiceover(
    narration: str,
    output: Path,
    config: TTSConfig,
    on_fallback: Callable[[str], TTSResult],
) -> TTSResult:
    temp_media = output.with_suffix(".edge.mp3")
    voice = _resolve_provider_voice(config.voice, EDGE_DEFAULT_VOICE)
    command = [
        sys.executable,
        "-m",
        "edge_tts",
        "--voice",
        voice,
        "--rate",
        config.edge_rate,
        "--text",
        narration,
        "--write-media",
        str(temp_media),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        temp_media.unlink(missing_ok=True)
        if config.allow_fallback:
            return on_fallback("edge-tts failed; fallback tone track generated.")
        raise TTSProviderError(
            "edge-tts failed. Install it with `python3 -m pip install edge-tts`, "
            "or pass --allow-fallback for a non-release rhythm guide track."
        ) from exc

    if _probe_duration_seconds(temp_media) <= 1:
        temp_media.unlink(missing_ok=True)
        if config.allow_fallback:
            return on_fallback("edge-tts returned unusable audio; fallback tone track generated.")
        raise TTSProviderError("edge-tts returned unusable audio.")

    try:
        _convert_audio(temp_media, output)
    except subprocess.CalledProcessError as exc:
        raise TTSProviderError("Failed to convert edge-tts audio to WAV.") from exc
    finally:
        temp_media.unlink(missing_ok=True)
    if _probe_duration_seconds(output) <= 1:
        raise TTSProviderError("Converted edge-tts audio is not usable.")
    notes = f"Voiceover generated with edge-tts voice {config.voice}.\n"
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="edge",
        voice=config.voice,
        model="edge-tts",
        used_fallback=False,
        notes=notes.strip(),
    )


def _synthesize_tone_voiceover(plan: VideoPlan, output: Path, reason: str) -> TTSResult:
    build_tone_track(plan, output)
    notes = (
        f"{reason} This is a non-release fallback rhythm guide. "
        "The narration is available in script.md and subtitles.srt.\n"
    )
    _write_voiceover_notes(output, notes)
    return TTSResult(
        path=output,
        provider="tone",
        voice="tone",
        model="local-tone",
        used_fallback=True,
        notes=notes.strip(),
    )


def _write_voiceover_notes(voiceover_path: Path, notes: str) -> None:
    voiceover_path.with_name("voiceover_notes.txt").write_text(notes, encoding="utf-8")


def _narration_text(plan: VideoPlan) -> str:
    return " ".join(segment.narration for segment in plan.segments)


def _build_script_markdown(plan: VideoPlan) -> str:
    lines = [
        "# Codex 自动化 AI 混剪短视频脚本",
        "",
        f"- 主题：{plan.config.topic}",
        f"- 对标标题：{plan.config.reference_title}",
        f"- 风格：{plan.config.style}",
        f"- 目标：{plan.config.goal}",
        f"- 规格：{plan.width}x{plan.height}, {plan.fps}fps, {plan.config.target_duration}s",
    ]
    if plan.config.style == "premium_studio_tutorial":
        lines.extend(
            [
                "- 事实来源：FIFA official fixtures page, Houston Chronicle match coverage, secondary schedule table",
                "- 边界声明：Prediction content is a model simulation, not a guaranteed match result.",
            ]
        )
    lines.extend(
        [
            "",
            "| 时间 | 段落 | 屏幕大字 | 口播 | English Subtitle | Key Points |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for segment in plan.segments:
        screen_text = segment.screen_text.replace("\n", " / ")
        key_points = " / ".join(segment.key_points)
        lines.append(
            f"| {segment.start}-{segment.end}s | {segment.role} | {screen_text} | "
            f"{segment.narration} | {segment.english_subtitle} | {key_points} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_storyboard_json(plan: VideoPlan) -> str:
    payload = {
        "config": asdict(plan.config),
        "width": plan.width,
        "height": plan.height,
        "fps": plan.fps,
        "segments": [asdict(segment) for segment in plan.segments],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _build_visual_prompts(plan: VideoPlan) -> str:
    aspect = "16:9 horizontal composition" if plan.width > plan.height else "9:16 vertical composition"
    if plan.config.style == "premium_studio_tutorial":
        shared = (
            "Shared constraints: dark sports-tech studio, AI silhouette presenter, neon green dashboards, "
            f"{aspect}, bilingual subtitles, original UI only, no copied presenter footage, no watermark."
        )
    else:
        shared = (
            f"Shared constraints: AI realistic montage, {aspect}, no copied Douyin footage, "
            "no watermark, no real private data."
        )
    lines = ["# Visual Prompts", "", shared, ""]
    for index, segment in enumerate(plan.segments, start=1):
        lines.extend(
            [
                f"## {index}. {segment.role} ({segment.start}-{segment.end}s)",
                f"Panel type: {segment.panel_type}",
                f"Key points: {'; '.join(segment.key_points)}",
                segment.visual_prompt,
                "",
            ]
        )
    return "\n".join(lines)


def _write_render_report(
    plan: VideoPlan,
    tts_result: TTSResult,
    video_path: Path,
    cover_path: Path,
    report_path: Path,
    frames: Sequence[Path],
    render_mode: str = "frame_concat",
    asset_manifest: Path | None = None,
) -> None:
    payload = {
        "config": asdict(plan.config),
        "video": {
            "path": str(video_path),
            "width": plan.width,
            "height": plan.height,
            "fps": plan.fps,
            "duration": plan.config.target_duration,
        },
        "tts": {
            "provider": tts_result.provider,
            "voice": tts_result.voice,
            "model": tts_result.model,
            "used_fallback": tts_result.used_fallback,
            "notes": tts_result.notes,
            "path": str(tts_result.path),
        },
        "artifacts": {
            "cover": str(cover_path),
            "frames_count": len(frames),
            "segments_count": len(plan.segments),
            "render_mode": render_mode,
            "asset_manifest": str(asset_manifest) if asset_manifest else "",
        },
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_srt(plan: VideoPlan) -> str:
    entries = []
    for index, segment in enumerate(plan.segments, start=1):
        subtitle_lines = [segment.narration]
        if segment.english_subtitle:
            subtitle_lines.append(segment.english_subtitle)
        entries.extend(
            [
                str(index),
                f"{_format_srt_time(segment.start)} --> {_format_srt_time(segment.end)}",
                "\n".join(subtitle_lines),
                "",
            ]
        )
    return "\n".join(entries)


def _format_srt_time(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02}:{minutes:02}:{secs:02},000"


def _render_frames(plan: VideoPlan, frames_dir: Path, frames_per_segment: int = 6) -> List[Path]:
    for stale_frame in frames_dir.glob("*.png"):
        stale_frame.unlink()

    frames = []
    for index, segment in enumerate(plan.segments):
        for keyframe in range(frames_per_segment):
            progress = keyframe / max(frames_per_segment - 1, 1)
            frame_path = frames_dir / f"frame_{index:02d}_{keyframe:02d}_{segment.role}.png"
            _draw_segment_frame(plan, segment, index, frame_path, progress)
            frames.append(frame_path)
    return frames
def _write_voiceover(plan: VideoPlan, voiceover_path: Path) -> None:
    narration = " ".join(segment.narration for segment in plan.segments)
    notes_path = voiceover_path.with_name("voiceover_notes.txt")
    say_path = shutil.which("say")
    if say_path:
        temp_say_path = voiceover_path.with_suffix(".say.aiff")
        subprocess.run([say_path, "-v", plan.config.voice, "-o", str(temp_say_path), narration], check=True)
        if _probe_duration_seconds(temp_say_path) > 1:
            _convert_audio(temp_say_path, voiceover_path)
            temp_say_path.unlink(missing_ok=True)
            notes_path.write_text("Voiceover generated with macOS say.\n", encoding="utf-8")
            return
        temp_say_path.unlink(missing_ok=True)

    build_tone_track(plan, voiceover_path)
    notes_path.write_text(
        "macOS say did not produce usable speech audio in this environment; "
        "Codex generated an audible rhythm guide track instead. "
        "The narration is available in script.md and subtitles.srt.\n",
        encoding="utf-8",
    )


def _convert_audio(input_path: Path, output_path: Path) -> None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        shutil.copyfile(input_path, output_path)
        return
    subprocess.run(
        [ffmpeg_path, "-y", "-i", str(input_path), "-ar", "44100", "-ac", "1", str(output_path)],
        check=True,
    )


def _probe_duration_seconds(path: Path) -> float:
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path or not path.exists():
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError:
        return 0.0
    payload = json.loads(result.stdout or "{}")
    try:
        return float(payload.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _write_concat_file(plan: VideoPlan, frames: Sequence[Path], concat_path: Path) -> None:
    lines = []
    frames_per_segment = max(1, len(frames) // len(plan.segments))
    frame_index = 0
    for segment in plan.segments:
        frame_duration = segment.duration / frames_per_segment
        for _ in range(frames_per_segment):
            frame = frames[frame_index]
            lines.append(f"file '{_escape_concat_path(frame)}'")
            lines.append(f"duration {frame_duration:.4f}")
            frame_index += 1
    lines.append(f"file '{_escape_concat_path(frames[-1])}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_segment_concat_file(segment_videos: Sequence[Path], concat_path: Path) -> None:
    lines = [f"file '{_escape_concat_path(path)}'" for path in segment_videos]
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_segment_videos_with_audio(
    segment_videos: Sequence[Path],
    audio_path: Path,
    output_path: Path,
    duration: int,
) -> None:
    concat_path = output_path.with_name("segments.txt")
    _write_segment_concat_file(segment_videos, concat_path)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-af",
            "apad",
            "-t",
            str(duration),
            str(output_path),
        ],
        check=True,
    )


def _extract_cover_frame(video_path: Path, cover_path: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", str(cover_path)], check=True)


def _escape_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def _draw_premium_studio_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float,
) -> None:
    image = Image.new("RGB", (plan.width, plan.height), "#06100d")
    draw = ImageDraw.Draw(image)
    _draw_premium_background(draw, plan.width, plan.height, index, progress)
    _draw_premium_presenter(draw, index, progress)
    _draw_premium_top_bar(draw, plan, segment, index, progress)
    _draw_premium_prediction_panel(draw, segment, index, progress)
    _draw_premium_side_panel(draw, segment, index, progress)
    _draw_premium_subtitles(draw, segment)
    image.save(output_path)


def _draw_premium_overlay_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float,
) -> None:
    image = Image.new("RGBA", (plan.width, plan.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_premium_top_bar(draw, plan, segment, index, progress)
    _draw_premium_prediction_panel(draw, segment, index, progress)
    _draw_premium_side_panel(draw, segment, index, progress)
    _draw_premium_subtitles(draw, segment)
    image.save(output_path)


def _draw_cinematic_broll_overlay_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float,
) -> None:
    image = Image.new("RGBA", (plan.width, plan.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_cinematic_match_strip(draw, plan, segment, index, progress)
    _draw_cinematic_score_chip(draw, segment, progress)
    _draw_cinematic_title(draw, segment)
    _draw_cinematic_key_tags(draw, segment)
    _draw_cinematic_subtitles(draw, segment)
    image.save(output_path)


def _render_premium_asset_segments(plan: VideoPlan, output_path: Path, manifest: Dict[str, object]) -> List[Path]:
    overlays_dir = output_path / "overlays"
    segments_dir = output_path / "segments"
    overlays_dir.mkdir(exist_ok=True)
    segments_dir.mkdir(exist_ok=True)
    for stale_overlay in overlays_dir.glob("*.png"):
        stale_overlay.unlink()
    for stale_segment in segments_dir.glob("*.mp4"):
        stale_segment.unlink()

    assets = manifest["assets"]
    asset_order = manifest["segment_asset_order"]
    assets_by_key = {asset["key"]: asset for asset in assets}
    rendered_segments = []
    for index, segment in enumerate(plan.segments):
        segment_asset_keys = asset_order[segment.role]
        base_duration = segment.duration // len(segment_asset_keys)
        remainder = segment.duration % len(segment_asset_keys)
        for asset_index, asset_key in enumerate(segment_asset_keys):
            asset = assets_by_key[asset_key]
            background_path = Path(asset["local_path"])
            if not background_path.exists():
                raise FileNotFoundError(f"Missing visual asset: {background_path}")
            overlay_path = overlays_dir / f"overlay_{index:02d}_{asset_index:02d}_{segment.role}.png"
            segment_path = segments_dir / f"segment_{index:02d}_{asset_index:02d}_{segment.role}.mp4"
            progress = (asset_index + 1) / len(segment_asset_keys)
            duration = base_duration + (1 if asset_index < remainder else 0)
            if manifest.get("overlay_style") == "cinematic_broll":
                _draw_cinematic_broll_overlay_frame(plan, segment, index, overlay_path, progress=progress)
            else:
                _draw_premium_overlay_frame(plan, segment, index, overlay_path, progress=progress)
            subprocess.run(
                build_segment_video_command(
                    background_path=background_path,
                    overlay_path=overlay_path,
                    output_path=segment_path,
                    duration=duration,
                    width=plan.width,
                    height=plan.height,
                    fps=plan.fps,
                    background_kind=asset["kind"],
                ),
                check=True,
            )
            rendered_segments.append(segment_path)
    return rendered_segments


def _draw_cinematic_match_strip(
    draw: ImageDraw.ImageDraw,
    plan: VideoPlan,
    segment: Segment,
    index: int,
    progress: float,
) -> None:
    draw.rounded_rectangle((44, 34, 662, 82), radius=12, fill=(4, 14, 12, 178), outline=(86, 214, 154, 180), width=1)
    draw.text((66, 45), "WORLD CUP 26  |  GROUP K", fill="#d8fff0", font=_font(22, bold=True))
    draw.rounded_rectangle((680, 51, 1240, 64), radius=6, fill=(14, 39, 31, 155))
    draw.rounded_rectangle((680, 51, 680 + int(560 * progress), 64), radius=6, fill=(245, 232, 76, 220))
    draw.rounded_rectangle((1370, 34, 1876, 128), radius=16, fill=(3, 12, 10, 174), outline=(58, 216, 142, 150), width=1)
    draw.text((1400, 48), f"{segment.start:03d}-{segment.end:03d}s", fill="#bdfce1", font=_font(21, bold=True))
    draw.text((1400, 81), f"Portugal vs DR Congo  |  {index + 1}/{len(plan.segments)}", fill="#ffffff", font=_font(25, bold=True))


def _draw_cinematic_score_chip(
    draw: ImageDraw.ImageDraw, segment: Segment, progress: float
) -> None:
    x0, y0, x1, y1 = 1370, 154, 1876, 344
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(3, 16, 13, 76), outline=(53, 216, 142, 165), width=2)
    draw.text((x0 + 28, y0 + 22), "MODEL PROJECTION", fill="#96ffd0", font=_font(20, bold=True))
    draw.text((x0 + 28, y0 + 55), "2 : 1", fill="#ffffff", font=_font(70, bold=True))
    bars = [
        ("POR", 0.62, "#35d88e"),
        ("DRAW", 0.22, "#f5e84c"),
        ("DRC", 0.16, "#ff6b7a"),
    ]
    for row, (label, value, color) in enumerate(bars):
        y = y0 + 124 + row * 32
        draw.text((x0 + 250, y - 6), label, fill="#d9fff0", font=_font(17, bold=True))
        draw.rounded_rectangle((x0 + 322, y, x1 - 28, y + 14), radius=7, fill=(23, 58, 47, 150))
        draw.rounded_rectangle(
            (x0 + 322, y, x0 + 322 + int((x1 - x0 - 350) * value * (0.86 + 0.14 * progress)), y + 14),
            radius=7,
            fill=color,
        )


def _draw_cinematic_title(draw: ImageDraw.ImageDraw, segment: Segment) -> None:
    title_lines = _wrap_premium_title_text(segment.screen_text, 16)
    y = 150
    for line in title_lines[:3]:
        bbox = draw.textbbox((0, 0), line, font=_font(46, bold=True))
        draw.rounded_rectangle((58, y - 7, 82 + bbox[2] - bbox[0], y + 54), radius=10, fill=(0, 0, 0, 72))
        draw.text((70, y), line, fill="#ffffff", font=_font(46, bold=True))
        y += 58


def _draw_cinematic_key_tags(draw: ImageDraw.ImageDraw, segment: Segment) -> None:
    x = 186
    y = 828
    for point in segment.key_points[:4]:
        text = point[:26]
        width = int(_font(22, bold=True).getlength(text)) + 34
        draw.rounded_rectangle((x, y, x + width, y + 42), radius=12, fill=(5, 30, 23, 76), outline=(82, 214, 154, 95), width=1)
        draw.text((x + 17, y + 9), text, fill="#d9fff0", font=_font(22, bold=True))
        x += width + 14


def _draw_cinematic_subtitles(draw: ImageDraw.ImageDraw, segment: Segment) -> None:
    subtitle_y = 888
    draw.rounded_rectangle((160, subtitle_y - 12, 1760, 1054), radius=18, fill=(0, 0, 0, 170))
    chinese_font = _font(34, bold=True)
    chinese_lines = _wrap_text_to_pixel_width(segment.narration, chinese_font, 1450)[:2]
    y = subtitle_y + 8
    for line in chinese_lines:
        bbox = draw.textbbox((0, 0), line, font=chinese_font)
        draw.text(((1920 - (bbox[2] - bbox[0])) / 2, y), line, fill="#ffffff", font=chinese_font)
        y += 43
    if segment.english_subtitle:
        english_font = _font(23)
        english = segment.english_subtitle
        bbox = draw.textbbox((0, 0), english, font=english_font)
        x = max(190, (1920 - (bbox[2] - bbox[0])) / 2)
        draw.text((x, 1014), english, fill="#9fffd4", font=english_font)


def _draw_premium_background(
    draw: ImageDraw.ImageDraw, width: int, height: int, index: int, progress: float
) -> None:
    top_rgb = _hex_to_rgb("#071512")
    bottom_rgb = _hex_to_rgb("#020403")
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top_rgb[c] * (1 - ratio) + bottom_rgb[c] * ratio) for c in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    green_x = 1390 + int(math.sin(progress * math.pi) * 24)
    magenta_x = 360 - int(math.sin(progress * math.pi) * 18)
    for radius, alpha_color in ((420, "#0f5f45"), (300, "#123d35"), (190, "#1b8061")):
        draw.ellipse(
            (green_x - radius, 120 - radius, green_x + radius, 120 + radius),
            outline=alpha_color,
            width=10,
        )
    for radius, color in ((340, "#4a1530"), (230, "#6f1b43")):
        draw.ellipse(
            (magenta_x - radius, 510 - radius, magenta_x + radius, 510 + radius),
            outline=color,
            width=8,
        )
    for x in range(0, width, 96):
        draw.line((x, 0, x - 220, height), fill="#0a211b", width=1)
    draw.rectangle((0, height - 86, width, height), fill="#020403")


def _draw_premium_presenter(draw: ImageDraw.ImageDraw, index: int, progress: float) -> None:
    center_x = 420 + int(math.sin(progress * math.pi) * 12)
    shoulder_y = 820
    draw.ellipse((center_x - 82, 285, center_x + 82, 449), fill="#1d2a28")
    draw.ellipse((center_x - 72, 294, center_x + 72, 438), outline="#35d88e", width=3)
    draw.rounded_rectangle((center_x - 178, 438, center_x + 178, shoulder_y), radius=92, fill="#101817")
    draw.polygon(
        [(center_x - 68, 454), (center_x + 68, 454), (center_x + 30, 690), (center_x - 30, 690)],
        fill="#dfe7e2",
    )
    draw.line((center_x - 155, 520, center_x - 260, 670), fill="#23302d", width=42)
    draw.line((center_x + 155, 520, center_x + 270, 682), fill="#23302d", width=42)
    draw.ellipse((center_x - 38, 348, center_x - 20, 366), fill="#96ffd0")
    draw.ellipse((center_x + 20, 348, center_x + 38, 366), fill="#96ffd0")
    draw.arc((center_x - 34, 382, center_x + 34, 420), 10, 170, fill="#96ffd0", width=4)
    draw.rounded_rectangle((180, 780, 660, 842), radius=18, fill="#071512", outline="#35d88e", width=2)
    draw.text((210, 795), "AI SKILL COMMENTARY", fill="#96ffd0", font=_font(30, bold=True))


def _draw_premium_top_bar(
    draw: ImageDraw.ImageDraw,
    plan: VideoPlan,
    segment: Segment,
    index: int,
    progress: float,
) -> None:
    draw.rounded_rectangle((46, 34, 780, 96), radius=18, fill="#06100d", outline="#35d88e", width=2)
    draw.text((74, 49), "FIFA WORLD CUP 26  |  GROUP K", fill="#96ffd0", font=_font(28, bold=True))
    draw.rounded_rectangle((1325, 34, 1876, 96), radius=18, fill="#06100d", outline="#243b35", width=2)
    draw.text((1354, 49), f"{segment.start:03d}-{segment.end:03d}s  {segment.role}", fill="#d9fff0", font=_font(26, bold=True))
    draw.rounded_rectangle((46, 112, 1876, 126), radius=7, fill="#123d35")
    draw.rounded_rectangle((46, 112, 46 + int(1830 * progress), 126), radius=7, fill="#f5e84c")


def _draw_premium_prediction_panel(
    draw: ImageDraw.ImageDraw, segment: Segment, index: int, progress: float
) -> None:
    x0, y0, x1, y1 = 810, 158, 1818, 694
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24, fill="#082119", outline="#35d88e", width=4)
    draw.rounded_rectangle((x0 + 24, y0 + 24, x1 - 24, y0 + 82), radius=16, fill="#1a6f4f")
    draw.text((x0 + 50, y0 + 38), "PORTUGAL  VS  DR CONGO", fill="#d9fff0", font=_font(38, bold=True))
    draw.text((x0 + 58, y0 + 128), "2", fill="#ffffff", font=_font(150, bold=True))
    draw.text((x0 + 210, y0 + 174), ":", fill="#f5e84c", font=_font(92, bold=True))
    draw.text((x0 + 292, y0 + 128), "1", fill="#ffffff", font=_font(150, bold=True))
    draw.text((x0 + 470, y0 + 158), "MODEL PROJECTION", fill="#96ffd0", font=_font(34, bold=True))
    bars = [
        ("Portugal win", 0.62, "#35d88e"),
        ("Draw", 0.22, "#f5e84c"),
        ("DR Congo win", 0.16, "#ff6b7a"),
    ]
    for row, (label, value, color) in enumerate(bars):
        y = y0 + 310 + row * 62
        draw.text((x0 + 58, y), label, fill="#d9fff0", font=_font(28, bold=True))
        draw.rounded_rectangle((x0 + 300, y + 8, x1 - 72, y + 34), radius=13, fill="#14342b")
        draw.rounded_rectangle(
            (x0 + 300, y + 8, x0 + 300 + int((x1 - x0 - 372) * value * (0.84 + 0.16 * progress)), y + 34),
            radius=13,
            fill=color,
        )
    if segment.panel_type == "simulation":
        draw.rounded_rectangle((x0 + 58, y1 - 96, x1 - 58, y1 - 42), radius=14, fill="#02110d")
        draw.text((x0 + 82, y1 - 82), "scenario: early goal / 0-0 halftime / DR Congo first goal", fill="#96ffd0", font=_font(25))


def _draw_premium_side_panel(
    draw: ImageDraw.ImageDraw, segment: Segment, index: int, progress: float
) -> None:
    x0, y0 = 84, 160
    title_lines = _wrap_premium_title_text(segment.screen_text, 13)
    y = y0
    for line in title_lines[:3]:
        draw.text((x0, y), line, fill="#ffffff", font=_font(58, bold=True))
        y += 72
    card_y = 650
    draw.rounded_rectangle((760, 732, 1818, 896), radius=22, fill="#06100d", outline="#243b35", width=2)
    draw.text((790, 754), "KEY VARIABLES", fill="#f5e84c", font=_font(30, bold=True))
    for point_index, point in enumerate(segment.key_points[:4]):
        px = 800 + point_index * 250
        draw.rounded_rectangle((px, 806, px + 210, 852), radius=12, fill="#10372a")
        draw.text((px + 14, 818), point[:22], fill="#d9fff0", font=_font(20, bold=True))
    draw.rounded_rectangle((84, card_y, 684, 735), radius=20, fill="#06100d", outline="#35d88e", width=2)
    draw.text((112, card_y + 24), "Prediction is a model estimate, not a certainty.", fill="#d9fff0", font=_font(24))


def _draw_premium_subtitles(draw: ImageDraw.ImageDraw, segment: Segment) -> None:
    subtitle_y = 912
    draw.rounded_rectangle((180, subtitle_y - 18, 1740, 1062), radius=20, fill="#020403", outline="#14251f", width=2)
    chinese_font = _font(36, bold=True)
    chinese_lines = _wrap_text_to_pixel_width(segment.narration, chinese_font, 1480)[:2]
    y = subtitle_y
    for line in chinese_lines:
        bbox = draw.textbbox((0, 0), line, font=chinese_font)
        draw.text(((1920 - (bbox[2] - bbox[0])) / 2, y), line, fill="#ffffff", font=chinese_font)
        y += 46
    if segment.english_subtitle:
        english = segment.english_subtitle
        bbox = draw.textbbox((0, 0), english, font=_font(25))
        x = max(210, (1920 - (bbox[2] - bbox[0])) / 2)
        draw.text((x, 1018), english, fill="#96ffd0", font=_font(25))


def _draw_segment_frame(
    plan: VideoPlan,
    segment: Segment,
    index: int,
    output_path: Path,
    progress: float = 0.0,
) -> None:
    if plan.config.style == "premium_studio_tutorial":
        _draw_premium_studio_frame(plan, segment, index, output_path, progress)
        return
    image = Image.new("RGB", (plan.width, plan.height), "#111827")
    draw = ImageDraw.Draw(image)
    _draw_gradient(draw, plan.width, plan.height, index, progress)
    _draw_synthetic_presenter(draw, plan.width, plan.height, index, progress)
    _draw_dashboard(draw, plan.width, plan.height, index, progress)
    _draw_text_layers(draw, plan, segment, index, progress)
    image.save(output_path)


def _draw_gradient(draw: ImageDraw.ImageDraw, width: int, height: int, index: int, progress: float) -> None:
    palettes = [
        ("#0f172a", "#0d9488"),
        ("#111827", "#dc2626"),
        ("#172554", "#f59e0b"),
        ("#064e3b", "#2563eb"),
        ("#312e81", "#16a34a"),
        ("#18181b", "#ea580c"),
        ("#083344", "#be123c"),
    ]
    top, bottom = palettes[index % len(palettes)]
    top_rgb = _hex_to_rgb(top)
    bottom_rgb = _hex_to_rgb(bottom)
    for y in range(height):
        ratio = min(1.0, max(0.0, y / height + (progress - 0.5) * 0.04))
        color = tuple(
            int(top_rgb[channel] * (1 - ratio) + bottom_rgb[channel] * ratio)
            for channel in range(3)
        )
        draw.line([(0, y), (width, y)], fill=color)


def _draw_synthetic_presenter(
    draw: ImageDraw.ImageDraw, width: int, height: int, index: int, progress: float
) -> None:
    center_x = 300 + (index % 2) * 460 + int(math.sin(progress * math.pi) * 18)
    shoulder_y = 1130
    skin = "#f1c27d"
    jacket = "#111827"
    shirt = "#f8fafc"
    accent = ["#22d3ee", "#f97316", "#a3e635", "#f43f5e"][index % 4]

    draw.ellipse((center_x - 95, 520, center_x + 95, 710), fill=skin)
    draw.rounded_rectangle((center_x - 165, 705, center_x + 165, shoulder_y), radius=95, fill=jacket)
    draw.polygon(
        [
            (center_x - 70, 720),
            (center_x + 70, 720),
            (center_x + 35, 960),
            (center_x - 35, 960),
        ],
        fill=shirt,
    )
    draw.rounded_rectangle((center_x - 42, 742, center_x + 42, 835), radius=18, fill=accent)
    draw.ellipse((center_x - 54, 590, center_x - 24, 620), fill="#111827")
    draw.ellipse((center_x + 24, 590, center_x + 54, 620), fill="#111827")
    draw.arc((center_x - 38, 628, center_x + 38, 675), 8, 172, fill="#7f1d1d", width=7)
    draw.rounded_rectangle((center_x + 140, 820, center_x + 250, 960), radius=55, fill=skin)
    draw.rounded_rectangle((center_x + 215, 660, center_x + 405, 1000), radius=38, fill="#020617")
    draw.rounded_rectangle((center_x + 238, 700, center_x + 382, 950), radius=24, fill="#e0f2fe")
    draw.rectangle((center_x + 258, 770, center_x + 360, 792), fill=accent)
    draw.rectangle((center_x + 258, 820, center_x + 348, 840), fill="#334155")
    draw.rectangle((center_x + 258, 870, center_x + 372, 890), fill="#334155")


def _draw_dashboard(draw: ImageDraw.ImageDraw, width: int, height: int, index: int, progress: float) -> None:
    x0 = 92 if index % 2 else 575
    y0 = 1055 - int(math.sin(progress * math.pi) * 16)
    x1 = x0 + 410
    y1 = y0 + 445
    draw.rounded_rectangle((x0, y0, x1, y1), radius=36, fill="#f8fafc")
    draw.rounded_rectangle((x0 + 28, y0 + 30, x1 - 28, y0 + 92), radius=22, fill="#0f172a")
    draw.text((x0 + 52, y0 + 45), "CREATOR DATA", fill="#ffffff", font=_font(34, bold=True))
    colors = ["#06b6d4", "#f97316", "#22c55e"]
    values = [0.55 + 0.20 * progress, 0.40 + 0.13 * progress, 0.66 + 0.20 * progress]
    labels = ["完播", "互动", "垂直"]
    for row, (label, value, color) in enumerate(zip(labels, values, colors)):
        y = y0 + 135 + row * 86
        draw.text((x0 + 46, y), label, fill="#0f172a", font=_font(34, bold=True))
        draw.rounded_rectangle((x0 + 145, y + 9, x1 - 45, y + 39), radius=15, fill="#e2e8f0")
        draw.rounded_rectangle(
            (x0 + 145, y + 9, x0 + 145 + int((x1 - x0 - 190) * value), y + 39),
            radius=15,
            fill=color,
        )
    chart_base = y1 - 76
    points = [
        (x0 + 52, chart_base - 25),
        (x0 + 130, chart_base - 75),
        (x0 + 210, chart_base - 50),
        (x0 + 285, chart_base - 120),
        (x0 + 358, chart_base - 155),
    ]
    draw.line(points, fill="#dc2626", width=10, joint="curve")
    for point in points:
        draw.ellipse((point[0] - 11, point[1] - 11, point[0] + 11, point[1] + 11), fill="#dc2626")


def _draw_text_layers(
    draw: ImageDraw.ImageDraw, plan: VideoPlan, segment: Segment, index: int, progress: float
) -> None:
    title_font = _font(86, bold=True)
    small_font = _font(35)
    caption_font = _font(42)

    label = f"{segment.start}-{segment.end}s  {segment.role.upper()}"
    draw.rounded_rectangle((74, 82, 1005, 148), radius=24, fill="#f8fafc")
    draw.text((105, 98), label, fill="#0f172a", font=_font(31, bold=True))
    draw.rounded_rectangle((74, 162, 1005, 178), radius=8, fill="#334155")
    draw.rounded_rectangle((74, 162, 74 + int(931 * progress), 178), radius=8, fill="#facc15")

    headline_lines = _wrap_by_chars(segment.screen_text, 10)
    y = 205 - int((1 - progress) * 18)
    for line in headline_lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        draw.text(((plan.width - (bbox[2] - bbox[0])) / 2, y), line, fill="#ffffff", font=title_font)
        y += 102

    keyword = ["收益公开", "完播优先", "垂直选题", "3秒结果", "案例证明", "复用公式", "评论分账"][
        index
    ]
    keyword_lift = int(math.sin(progress * math.pi) * 12)
    draw.rounded_rectangle((90, 1538 - keyword_lift, 990, 1626 - keyword_lift), radius=28, fill="#facc15")
    bbox = draw.textbbox((0, 0), keyword, font=_font(52, bold=True))
    draw.text(
        ((plan.width - (bbox[2] - bbox[0])) / 2, 1554 - keyword_lift),
        keyword,
        fill="#111827",
        font=_font(52, bold=True),
    )

    caption = wrap_video_text(segment.narration, 21)
    y = 1670
    for line in caption[:3]:
        bbox = draw.textbbox((0, 0), line, font=caption_font)
        draw.rounded_rectangle(
            (58, y - 10, 1022, y + 58),
            radius=18,
            fill=(15, 23, 42),
        )
        draw.text(((plan.width - (bbox[2] - bbox[0])) / 2, y), line, fill="#f8fafc", font=caption_font)
        y += 74

    draw.text(
        (88, 1840),
        f"Topic: {plan.config.topic}",
        fill="#e2e8f0",
        font=small_font,
    )


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return load_font(size, bold)


def _wrap_by_chars(text: str, limit: int) -> List[str]:
    return wrap_video_text(text, limit)


def _wrap_premium_title_text(text: str, limit: int) -> List[str]:
    lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "DR Congo" in line and len(line) > limit:
            prefix = line.replace("DR Congo", "").strip()
            if prefix:
                lines.extend(wrap_video_text(prefix, limit))
            lines.append("DR Congo")
            continue
        lines.extend(wrap_video_text(line, limit))
    return lines or [text]


def _wrap_text_to_pixel_width(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for char in text:
        if char == "\n":
            if current:
                lines.append(current)
                current = ""
            continue
        candidate = current + char
        if current and font.getlength(candidate) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
