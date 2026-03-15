from pydantic import BaseModel


class ExtractedEntity(BaseModel):
    name: str
    entity_type: str
    aliases: list[str] = []
    context_tags: list[str] = []


class EntityRelation(BaseModel):
    source_name: str
    relation_type: str
    evidence_type: str
    evidence_id: str
