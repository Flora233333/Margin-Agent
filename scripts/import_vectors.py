"""把 RC6-C 的向量索引（Chroma）原样导入 PostgreSQL 的 block_vectors 表。只需运行一次。

用法：
    conda run -n margin --no-capture-output python scripts/import_vectors.py <chroma 目录>
    例：... scripts/import_vectors.py .cache/chroma/rc6-local-qwen3emb06b-q8_0-v2/chroma

为什么导入而不是重新计算：文档向量是 RC6-C 训练 / 评测时用的那一份（服务器 GPU 建库），
原样搬过来，检索结果才和训练环境一致；CPU 上重算 1.76 万块要几十个小时。
数据库地址读 .env 的 MARGIN_DATABASE_URL。表会先清空再导入，重复运行结果一样。
M1 引入 Alembic 后，建表语句移到迁移脚本里。
"""

from __future__ import annotations

import argparse
import os
import time

import chromadb
import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from run_episode import load_dotenv

# vector(1024)：Qwen3-Embedding-0.6B 的向量维度，写进列类型，维度不对的数据插不进去
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS block_vectors (
    block_id  text PRIMARY KEY,
    doc_id    text NOT NULL,
    embedding vector(1024) NOT NULL
)
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("chroma_path", help="Chroma 持久化目录（里面有 chroma.sqlite3）")
    args = parser.parse_args()
    load_dotenv()

    collection = chromadb.PersistentClient(path=args.chroma_path).get_collection(
        "afacpt_canonical_blocks_v1")
    data = collection.get(include=["embeddings", "metadatas"])
    print(f"从 Chroma 读出 {len(data['ids'])} 条向量")

    with psycopg.connect(os.environ["MARGIN_DATABASE_URL"]) as conn:
        # pgvector 是 PG 扩展，每个数据库要先启用一次，之后才有 vector 类型
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        conn.execute(CREATE_TABLE)
        conn.execute("TRUNCATE block_vectors")
        started = time.monotonic()
        # COPY 是 PG 的批量导入协议，比逐行 INSERT 快一两个数量级
        with conn.cursor().copy(
            "COPY block_vectors (block_id, doc_id, embedding) FROM STDIN WITH (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["text", "text", "vector"])
            for block_id, meta, vector in zip(
                data["ids"], data["metadatas"], data["embeddings"], strict=True
            ):
                copy.write_row((block_id, meta["doc_id"], np.asarray(vector, dtype=np.float32)))
        count = conn.execute("SELECT count(*) FROM block_vectors").fetchone()[0]
        # with 块正常结束时 psycopg 自动提交事务；中途出错则整体回滚，不会留下半张表
    print(f"导入 {count} 条，耗时 {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
