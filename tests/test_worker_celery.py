"""worker 的 Celery 配置里，和 RabbitMQ 打交道、出过事故的那几项。"""

from margin.worker import celery_app


def test_prefetch_is_not_lowered_after_broker_reconnect():
    """RabbitMQ 重启、worker 重连时手上还有任务在执行：Celery 5.6.3 默认把预取数从 4 临时调到 3，
    之后只改了自己记的数、没有通知 RabbitMQ，worker 从此只拿 3 条（M1.6 实验 V8 发现）。
    dispatcher 按 4 个空位发，第 4 条就一直在 RabbitMQ 里排队，重发也被暂停。
    所以必须关掉这个“临时调低”，预取数始终等于子进程数。"""
    assert celery_app.conf.worker_enable_prefetch_count_reduction is False
    assert celery_app.conf.worker_prefetch_multiplier == 1
