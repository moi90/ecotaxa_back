# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
#  Models used in Taxonomy API operations.
#
from typing import List, Optional

from pydantic import Field
from pydantic.main import BaseModel

from API_models.crud import ProjectSummaryModel
from API_models.helpers.DBtoModel import OrmConfig


class TaxaSearchRsp(BaseModel):
    id: int = Field(title="The taxon/category IDs.")
    renm_id: Optional[int] = Field(title="The advised replacement ID iif the taxon/category is deprecated.")
    text: str = Field(title="The taxon name, display one.")
    pr: int = Field(title="1 if the taxon is in project list, 0 otherwise.")


# TODO: dataclass_to_model(TaxonBO) to avoid repeated fields
class TaxonModel(BaseModel):
    __config__ = OrmConfig
    id: int = Field(title="The taxon/category IDs.")
    renm_id: Optional[int] = Field(title="The advised replacement ID iif the taxon/category is deprecated.")
    name: str = Field(title="The taxon/category verbatim name.")
    type: str = Field(title="The taxon/category type, 'M' or 'P'")
    nb_objects: int = Field(title="How many objects are classified in this category.")
    nb_children_objects: int = Field(title="How many objects are classified in this category children (not itself).")
    display_name: str = Field(title="The taxon/category display name.")
    lineage: List[str] = Field(title="The taxon/category name of ancestors, including self, in first.")
    id_lineage: List[int] = Field(title="The taxon/category IDs of ancestors, including self, in first.")
    children: List[int] = Field(title="The taxon/category IDs of children.")


class TaxonUsageModel(ProjectSummaryModel):
    nb_validated: int= Field(title="How many validated objects in this category in this project")

class TaxonomyTreeStatus(BaseModel):
    last_refresh: Optional[str] = Field(title="Taxonomy tree last refresh/sync from taxonomy server. "
                                              "Date, with format YYYY-MM-DDThh:mm:ss.")
