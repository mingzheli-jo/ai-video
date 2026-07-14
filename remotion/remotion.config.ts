import { Config } from "@remotion/cli/config";

// 透明背景：ProRes 4444 带 alpha 通道，供 ffmpeg overlay 合成。
Config.setVideoImageFormat("png");
Config.setPixelFormat("yuva444p10le");
Config.setCodec("prores");
Config.setProResProfile("4444");
