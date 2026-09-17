"""모든 모델을 하나의 MetaData 에 모으는 조립 지점.

여기와 alembic env.py 만 다른 모듈의 ``models`` 를 import 한다.
서비스 코드는 절대 이 모듈을 import 하지 않는다 (규칙 2).
"""

from app.authoring.ass import models as ass_models
from app.authoring.nlcd import models as nlcd_models
from app.authoring.rex import models as rex_models
from app.content.manuscripts import models as manuscript_models
from app.db.base import Base
from app.events import models as outbox_models
from app.insight.aiq import models as aiq_models
from app.insight.fts import models as fts_models
from app.insight.rcv import models as rcv_models
from app.insight.scds import models as scds_models
from app.insight.ssm import models as ssm_models
from app.jobs import models as job_models
from app.platform_.auth import models as auth_models
from app.platform_.notifications import models as notification_models
from app.platform_.projects import models as project_models
from app.platform_.teams import models as team_models

__all__ = ["Base"]

_MODULES = (
    auth_models,
    team_models,
    project_models,
    notification_models,
    manuscript_models,
    nlcd_models,
    ass_models,
    rex_models,
    scds_models,
    ssm_models,
    aiq_models,
    rcv_models,
    fts_models,
    job_models,
    outbox_models,
)
