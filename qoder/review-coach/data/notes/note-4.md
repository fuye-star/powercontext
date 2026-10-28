---
title: 浏览器端推理后端
topic: 浏览器
---
WebGPU 允许网页直接调用 GPU 并行计算，适合运行 ONNX 量化模型。不支持 WebGPU 时回退到 WASM，由 CPU 执行推理，速度较慢但兼容性更好。Transformers.js 在加载模型时选择 device 与 dtype，页面应明确提示当前使用的后端，避免用户误判性能。
