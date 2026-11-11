from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .graphs.common import active_snapshot
from .models import Decision, TaskInput, validate_decision


SCHEMA = """
CREATE TABLE IF NOT EXISTS content_tasks (
    id text PRIMARY KEY,
    direction text NOT NULL,
    status text NOT NULL,
    state jsonb NOT NULL,
    interrupt_id text,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS content_jobs (
    id bigserial PRIMARY KEY,
    task_id text NOT NULL REFERENCES content_tasks(id),
    kind text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'pending'
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job
    ON content_jobs(task_id) WHERE status IN ('pending', 'running');
CREATE TABLE IF NOT EXISTS content_history (
    task_id text NOT NULL REFERENCES content_tasks(id),
    checkpoint_id text NOT NULL,
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS content_node_runs (
    id bigserial PRIMARY KEY,
    task_id text NOT NULL REFERENCES content_tasks(id),
    job_id bigint NOT NULL REFERENCES content_jobs(id),
    subgraph text NOT NULL,
    node text NOT NULL,
    node_name text NOT NULL,
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    duration_ms double precision,
    input_tokens bigint,
    output_tokens bigint,
    total_tokens bigint,
    model text,
    output_text text NOT NULL DEFAULT ''
);
ALTER TABLE content_node_runs ADD COLUMN IF NOT EXISTS output_text text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS node_runs_by_task ON content_node_runs(task_id, id);
"""


class Store:
    def __init__(self, url: str):
        self.url = url

    def connect(self):
        return psycopg.connect(self.url, row_factory=dict_row)

    def setup(self):
        with self.connect() as conn:
            conn.execute(SCHEMA)

    def create(self, brief: TaskInput):
        task_id = str(uuid4())
        state = {"task_id": task_id, "brief": brief.model_dump()}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO content_tasks(id, direction, status, state) VALUES (%s,%s,'queued',%s)",
                (task_id, brief.direction, Jsonb(state)),
            )
            conn.execute("INSERT INTO content_jobs(task_id,kind) VALUES (%s,'start')", (task_id,))
        return self.get(task_id)

    def list(self):
        with self.connect() as conn:
            return conn.execute(
                "SELECT id,direction,status,error,created_at,updated_at FROM content_tasks ORDER BY created_at DESC LIMIT 100"
            ).fetchall()

    def get(self, task_id):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM content_tasks WHERE id=%s", (task_id,)).fetchone()

    def history(self, task_id):
        with self.connect() as conn:
            return conn.execute(
                "SELECT checkpoint_id,state,created_at FROM content_history WHERE task_id=%s ORDER BY created_at",
                (task_id,),
            ).fetchall()

    def node_runs(self, task_id):
        with self.connect() as conn:
            return conn.execute(
                "SELECT id,task_id,job_id,subgraph,node,node_name,status,started_at,finished_at,"
                "duration_ms,input_tokens,output_tokens,total_tokens,model "
                "FROM content_node_runs WHERE task_id=%s ORDER BY id", (task_id,),
            ).fetchall()

    def append_node_output(self, run_id, text):
        with self.connect() as conn:
            conn.execute("UPDATE content_node_runs SET output_text=output_text || %s WHERE id=%s", (text, run_id))

    def latest_node_output(self, task_id):
        with self.connect() as conn:
            return conn.execute(
                "SELECT id,job_id,node,node_name,status,output_text FROM content_node_runs "
                "WHERE task_id=%s AND node IN ('propose_topics','write_article') "
                "AND job_id=(SELECT max(id) FROM content_jobs WHERE task_id=%s) ORDER BY id DESC LIMIT 1",
                (task_id, task_id),
            ).fetchone()

    def start_node(self, task_id, job_id, subgraph, node, node_name):
        with self.connect() as conn:
            return conn.execute(
                "INSERT INTO content_node_runs(task_id,job_id,subgraph,node,node_name) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (task_id, job_id, subgraph, node, node_name),
            ).fetchone()["id"]

    def finish_node(self, run_id, status, duration_ms):
        with self.connect() as conn:
            conn.execute(
                "UPDATE content_node_runs SET status=%s,duration_ms=%s,finished_at=now() WHERE id=%s",
                (status, duration_ms, run_id),
            )

    def set_node_model(self, run_id, model):
        with self.connect() as conn:
            conn.execute("UPDATE content_node_runs SET model=%s WHERE id=%s", (model, run_id))

    def set_node_usage(self, run_id, model, usage):
        # 每个生成节点调用一次模型，用量保留接口原值；缺失时存 NULL，不伪造为 0。
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        total = usage.get("total_tokens")
        if total is None and input_tokens is not None and output_tokens is not None:
            total = input_tokens + output_tokens
        with self.connect() as conn:
            conn.execute(
                "UPDATE content_node_runs SET model=%s,input_tokens=%s,output_tokens=%s,total_tokens=%s WHERE id=%s",
                (model, input_tokens, output_tokens, total, run_id),
            )

    def decide(self, task_id: str, decision: Decision):
        with self.connect() as conn:
            task = conn.execute("SELECT * FROM content_tasks WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
            if task is None:
                raise LookupError("任务不存在")
            if task["status"] != "waiting" or task["interrupt_id"] != decision.interrupt_id:
                raise ValueError("此审核已处理或版本已更新，请刷新页面")
            validate_decision(task["state"], decision)
            conn.execute(
                "INSERT INTO content_jobs(task_id,kind,payload) VALUES (%s,'resume',%s)",
                (task_id, Jsonb(decision.model_dump())),
            )
            conn.execute("UPDATE content_tasks SET status='queued',error=NULL,updated_at=now() WHERE id=%s", (task_id,))

    def retry(self, task_id):
        with self.connect() as conn:
            task = conn.execute("SELECT status FROM content_tasks WHERE id=%s FOR UPDATE", (task_id,)).fetchone()
            if task is None:
                raise LookupError("任务不存在")
            if task["status"] != "failed":
                raise ValueError("只有失败任务可以重试")
            # 重投原命令，由检查点判断实际执行位置。
            conn.execute(
                "INSERT INTO content_jobs(task_id,kind,payload) SELECT task_id,kind,payload FROM content_jobs "
                "WHERE task_id=%s ORDER BY id DESC LIMIT 1", (task_id,),
            )
            conn.execute("UPDATE content_tasks SET status='queued',error=NULL,updated_at=now() WHERE id=%s", (task_id,))

    def claim(self):
        with self.connect() as conn:
            job = conn.execute(
                "UPDATE content_jobs SET status='running' WHERE id=(SELECT id FROM content_jobs "
                "WHERE status='pending' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *"
            ).fetchone()
            if job:
                conn.execute("UPDATE content_tasks SET status='running',updated_at=now() WHERE id=%s", (job["task_id"],))
            return job

    def recover_running_jobs(self):
        with self.connect() as conn:
            conn.execute("UPDATE content_jobs SET status='pending' WHERE status='running'")
            conn.execute("UPDATE content_node_runs SET status='interrupted',finished_at=now() "
                         "WHERE status='running'")

    def finish(self, job, snapshot, error=None):
        state = dict(snapshot.values)
        state.pop("decision", None)
        pending = snapshot.interrupts
        status = "failed" if error else "waiting" if pending else state.get("stage", "failed")
        with self.connect() as conn:
            conn.execute(
                "UPDATE content_tasks SET status=%s,state=%s,interrupt_id=%s,error=%s,updated_at=now() WHERE id=%s",
                (status, Jsonb(state), pending[0].id if pending else None, error, job["task_id"]),
            )
            # 同一子图内多次退回时，主图检查点可能不变，历史应绑定内部最新检查点。
            config = active_snapshot(snapshot).config
            checkpoint_id = config.get("configurable", {}).get("checkpoint_id") if config else None
            if checkpoint_id:
                conn.execute(
                    "INSERT INTO content_history(task_id,checkpoint_id,state) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (job["task_id"], checkpoint_id, Jsonb(state)),
                )
            conn.execute("UPDATE content_jobs SET status=%s WHERE id=%s", ("failed" if error else "done", job["id"]))


def main():
    from langgraph.checkpoint.postgres import PostgresSaver

    settings = Settings()
    Store(settings.database_url).setup()
    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
    print("数据库和 LangGraph 检查点表已初始化")


if __name__ == "__main__":
    main()
