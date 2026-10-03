# 启动本机 embedding 服务（llama.cpp，纯 CPU），只给“查询”算向量。前台运行，Ctrl+C 停止。
#   pwsh scripts/start_embedding.ps1
#
# 参数与 RC6-C 的索引 rc6-local-qwen3emb06b-q8_0-v2 保持一致，改了就和库里的文档向量对不上：
#   --pooling last        Qwen3-Embedding 取最后一个 token 的隐藏状态作为整句向量
#   --embd-normalize 2    L2 归一化（向量长度为 1），余弦相似度 = 点积
#   -c / -b / -ub 8192    一段输入必须整段放进一个批次；默认 512 会拒绝长文本
#   --alias               对外的模型名，和索引记录的名字相同
# 程序在 bin/llama.cpp（官方 Windows CPU 版 b11379），模型在 models/（Q8_0，sha256 06507c7b…），都不进仓库。
& "$PSScriptRoot/../bin/llama.cpp/llama-server.exe" `
    -m "$PSScriptRoot/../models/Qwen3-Embedding-0.6B-Q8_0.gguf" `
    --embedding --pooling last --embd-normalize 2 -ngl 0 `
    -c 8192 -b 8192 -ub 8192 --threads 8 `
    --host 127.0.0.1 --port 18082 --alias rc6-local-qwen3emb06b-q8_0-v2
