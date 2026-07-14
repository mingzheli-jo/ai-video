from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import (
    TTSConfig,
    VideoConfig,
    VideoPlan,
    build_portugal_dr_congo_prediction_plan,
    build_video_plan,
    render_video,
    write_artifacts,
)

EDGE_TTS_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DOUBAO_TTS_DEFAULT_VOICE = "zh_male_liufei_uranus_bigtts"


def default_portugal_asset_manifest() -> Path:
    return Path(__file__).resolve().parent / "assets" / "portugal_dr_congo" / "asset_manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a vertical AI montage tutorial video.")
    parser.add_argument(
        "--template",
        choices=["creator-monetization", "portugal-dr-congo"],
        default="creator-monetization",
    )
    parser.add_argument("--topic", default=VideoConfig.topic)
    parser.add_argument("--reference-title", default=VideoConfig.reference_title)
    parser.add_argument("--duration", type=int, default=45)
    parser.add_argument("--style", default=VideoConfig.style)
    parser.add_argument("--goal", default=VideoConfig.goal)
    parser.add_argument("--voice", default=VideoConfig.voice)
    parser.add_argument("--voice-instructions", default=VideoConfig.voice_instructions)
    parser.add_argument("--tts-provider", choices=["openai", "edge", "doubao", "tone", "file"], default=VideoConfig.tts_provider)
    parser.add_argument("--edge-rate", default="+20%")
    parser.add_argument("--audio-file", type=Path)
    parser.add_argument("--allow-fallback", action="store_true")
    parser.add_argument("--release", action="store_true", default=True)
    parser.add_argument("--output", type=Path, default=Path("video_factory/output/release"))
    parser.add_argument("--visual-asset-manifest", type=Path, default=None)
    parser.add_argument("--script-only", action="store_true", help="Write text artifacts without rendering media.")
    return parser.parse_args(argv)


def build_tts_config(args: argparse.Namespace) -> TTSConfig:
    voice = args.voice
    if args.tts_provider == "edge" and voice == VideoConfig.voice:
        voice = EDGE_TTS_DEFAULT_VOICE
    if args.tts_provider == "doubao" and voice == VideoConfig.voice:
        voice = DOUBAO_TTS_DEFAULT_VOICE
    return TTSConfig(
        provider=args.tts_provider,
        voice=voice,
        voice_instructions=args.voice_instructions,
        audio_file=args.audio_file,
        allow_fallback=args.allow_fallback,
        edge_rate=args.edge_rate,
    )


def build_plan_from_args(args: argparse.Namespace) -> VideoPlan:
    if args.template == "portugal-dr-congo":
        config = VideoConfig(
            topic="Portugal vs DR Congo：AI Skill 世界杯赛前预测",
            target_duration=340,
            style="premium_studio_tutorial",
            goal="sports_prediction_retention",
            reference_title="C罗第六届世界杯首战，AI预测葡萄牙会不会翻车",
            output_slug=args.output.name,
            tts_provider=args.tts_provider,
            voice=build_tts_config(args).voice,
            voice_instructions=args.voice_instructions,
        )
        return build_portugal_dr_congo_prediction_plan(config)

    config = VideoConfig(
        topic=args.topic,
        target_duration=args.duration,
        style=args.style,
        goal=args.goal,
        reference_title=args.reference_title,
        output_slug=args.output.name,
        tts_provider=args.tts_provider,
        voice=build_tts_config(args).voice,
        voice_instructions=args.voice_instructions,
    )
    return build_video_plan(config)


def main() -> None:
    args = parse_args()

    plan = build_plan_from_args(args)

    if args.script_only:
        paths = write_artifacts(plan, args.output)
        for key, path in paths.items():
            print(f"{key}: {path}")
        return

    visual_asset_manifest = args.visual_asset_manifest
    if visual_asset_manifest is None and args.template == "portugal-dr-congo":
        candidate = default_portugal_asset_manifest()
        visual_asset_manifest = candidate if candidate.exists() else None

    result = render_video(
        plan,
        args.output,
        build_tts_config(args),
        release=args.release,
        visual_asset_manifest=visual_asset_manifest,
    )
    print(f"script: {result.script}")
    print(f"storyboard: {result.storyboard}")
    print(f"prompts: {result.prompts}")
    print(f"subtitles: {result.subtitles}")
    print(f"voiceover: {result.voiceover}")
    print(f"cover: {result.cover}")
    print(f"report: {result.report}")
    print(f"video: {result.video}")


if __name__ == "__main__":
    main()
