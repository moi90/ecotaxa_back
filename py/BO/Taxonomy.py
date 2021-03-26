# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
# After SQL alchemy models are defined individually, setup the relations b/w them
#
from typing import List, Set, Dict, Tuple

from API_operations.TaxoManager import TaxonomyChangeService
from BO.Classification import ClassifIDCollT, ClassifIDT, ClassifIDListT
from DB import ResultProxy, Taxonomy, WoRMS
from DB.Project import ProjectTaxoStat
from DB.helpers.ORM import Session, Query, any_, case, func, text, select
from helpers.DynamicLogs import get_logger

ClassifSetInfoT = Dict[ClassifIDT, Tuple[str, str]]

logger = get_logger(__name__)


class TaxonBO(object):
    """
        Holder of a node of the taxonomy tree.
    """

    def __init__(self, cat_type: str, display_name: str, nb_objects: int, nb_children_objects: int,
                 lineage: List[str], id_lineage: List[ClassifIDT],
                 children: List[ClassifIDT] = None):
        assert cat_type in ('P', 'M')
        self.type = cat_type
        if children is None:
            children = []
        self.id = id_lineage[0]
        self.name = lineage[0]
        self.nb_objects = nb_objects if nb_objects is not None else 0
        self.nb_children_objects = nb_children_objects if nb_children_objects is not None else 0
        self.display_name = display_name
        self.lineage = lineage
        self.id_lineage = id_lineage
        self.children = children

    def top_down_lineage(self, sep: str = ">"):
        return sep.join(reversed(self.lineage))


class TaxonomyBO(object):
    """
        Holder for methods on taxonomy tree.
    """

    @staticmethod
    def find_ids(session: Session, classif_id_seen: List):
        """
            Return input IDs for the existing ones.
        """
        res: ResultProxy = session.execute(
            "SELECT id "
            "  FROM taxonomy "
            " WHERE id = ANY (:een)",
            {"een": list(classif_id_seen)})
        return {int(r['id']) for r in res}

    @staticmethod
    def keep_phylo(session: Session, classif_id_seen: ClassifIDListT):
        """
            Return input IDs, for the existing ones with 'P' type.
        """
        res: ResultProxy = session.execute(
            "SELECT id "
            "  FROM taxonomy "
            " WHERE id = ANY (:een) AND taxotype = 'P'",
            {"een": list(classif_id_seen)})
        return {an_id for an_id, in res}

    @staticmethod
    def resolve_taxa(session: Session, taxo_found, taxon_lower_list):
        """
            Match taxa in taxon_lower_list and return the matched ones in taxo_found.
        """
        res: ResultProxy = session.execute(
            """SELECT t.id, lower(t.name) AS name, lower(t.display_name) AS display_name, 
                      lower(t.name)||'<'||lower(p.name) AS computedchevronname 
                 FROM taxonomy t
                LEFT JOIN taxonomy p on t.parent_id = p.id
                WHERE lower(t.name) = ANY(:nms) OR lower(t.display_name) = ANY(:dms) 
                    OR lower(t.name)||'<'||lower(p.name) = ANY(:chv) """,
            {"nms": taxon_lower_list, "dms": taxon_lower_list, "chv": taxon_lower_list})
        for rec_taxon in res:
            for found_k, found_v in taxo_found.items():
                if ((found_k == rec_taxon['name'])
                        or (found_k == rec_taxon['display_name'])
                        or (found_k == rec_taxon['computedchevronname'])
                        or (('alterdisplayname' in found_v) and (
                                found_v['alterdisplayname'] == rec_taxon['display_name']))):
                    taxo_found[found_k]['nbr'] += 1
                    taxo_found[found_k]['id'] = rec_taxon['id']

    @staticmethod
    def names_with_parent_for(session: Session, id_coll: ClassifIDCollT) -> ClassifSetInfoT:
        """
            Get taxa names from id list.
        """
        ret = {}
        res: ResultProxy = session.execute(
            """SELECT t.id, t.name, p.name AS parent_name
                 FROM taxonomy t
                LEFT JOIN taxonomy p ON t.parent_id = p.id
                WHERE t.id = ANY(:ids) """,
            {"ids": list(id_coll)})
        for rec_taxon in res:
            ret[rec_taxon['id']] = (rec_taxon['name'], rec_taxon['parent_name'])
        return ret

    @staticmethod
    def children_of(session: Session, id_list: List[int]) -> Set[int]:
        """
            Get id and children taxa ids for given id.
        """
        res: ResultProxy = session.execute(
            """WITH RECURSIVE rq(id) 
                AS (SELECT id 
                      FROM taxonomy 
                     WHERE id = ANY(:ids)
                     UNION
                    SELECT t.id 
                      FROM rq 
                      JOIN taxonomy t ON rq.id = t.parent_id )
               SELECT id FROM rq """,
            {"ids": id_list})
        return {int(r['id']) for r in res}

    MAX_MATCHES = 200
    MAX_TAXONOMY_LEVELS = 20

    @classmethod
    def query(cls, session: Session,
              restrict_to: ClassifIDListT, priority_set: ClassifIDListT,
              display_name_filter: str, name_filters: List[str]):
        """
        :param session:
        :param restrict_to: If not None, limit the query to given IDs.
        :param priority_set: Regardless of MAX_MATCHES, these IDs must appear in the result if they match.
        :param display_name_filter:
        :param name_filters:
        :return:
        """
        tf = Taxonomy.__table__.alias('tf')
        # bind = None  # For portable SQL, no 'ilike'
        bind = session.get_bind()
        # noinspection PyTypeChecker
        priority = case([(tf.c.id == any_(priority_set), text('0'))], else_=text('1')).label('prio')
        qry = select([tf.c.taxotype, tf.c.id, tf.c.display_name, priority], bind=bind)
        if len(name_filters) > 0:
            # Add to the query enough to get the full hierarchy for filtering
            concat_all, qry = cls._add_recursive_query(qry, tf, do_concat=True)
            # Below is quite expensive
            taxo_lineage = func.concat(*concat_all)
            name_filter = "%<" + "".join(name_filters)  # i.e. anywhere consecutively in the lineage
            qry = qry.where(taxo_lineage.ilike(name_filter))
        if restrict_to is not None:
            qry = qry.where(tf.c.id == any_(restrict_to))
        # We have index IS_TaxonomyDispNameLow so this lower() is for free
        qry = qry.where(func.lower(tf.c.display_name).like(display_name_filter))
        qry = qry.order_by(priority, func.lower(tf.c.display_name))
        qry = qry.limit(cls.MAX_MATCHES)
        logger.info("Taxo query: %s with params %s and %s ", qry, display_name_filter, name_filters)
        res: ResultProxy = session.execute(qry)
        return res.fetchall()

    @classmethod
    def _add_recursive_query(cls, qry, tf, do_concat):
        # Build a query on names and hierarchy
        # Produced SQL looks like:
        #       left join taxonomy t1 on tf.parent_id=t1.id
        #       left join taxonomy t2 on t1.parent_id=t2.id
        # ...
        #       left join taxonomy t14 on t13.parent_id=t14.id
        lev_alias = Taxonomy.__table__.alias('t1')
        # Evntually, also build a concat to get e.g. a < b < c < d string
        if do_concat:
            lineage_sep = text("'<'")
            concat_all = [tf.c.name, lineage_sep, lev_alias.c.name]
        else:
            lineage_sep = None
            concat_all = None
        # Chain outer joins on Taxonomy
        # hook the first OJ to main select
        chained_joins = tf.join(lev_alias, lev_alias.c.id == tf.c.parent_id, isouter=True)
        prev_alias = lev_alias
        for level in range(2, cls.MAX_TAXONOMY_LEVELS):
            lev_alias = Taxonomy.__table__.alias('t%d' % level)
            # hook each following OJ to previous one
            chained_joins = chained_joins.join(lev_alias,
                                               lev_alias.c.id == prev_alias.c.parent_id,
                                               isouter=True)
            if concat_all:
                # Collect expressions
                concat_all.extend([lineage_sep, lev_alias.c.name])
            prev_alias = lev_alias
        qry = qry.select_from(chained_joins)
        return concat_all, qry


class TaxonBOSet(object):
    """
        Many taxa.
    """

    def __init__(self, session: Session, taxon_ids: ClassifIDListT):
        tf = Taxonomy.__table__.alias('tf')
        # bind = None  # For portable SQL, no 'ilike'
        bind = session.get_bind()
        select_list = [tf.c.taxotype, tf.c.nbrobj, tf.c.nbrobjcum, tf.c.display_name, tf.c.id, tf.c.name, ]
        select_list.extend([text("t%d.id, t%d.name" % (level, level))  # type:ignore
                            for level in range(1, TaxonomyBO.MAX_TAXONOMY_LEVELS)])
        qry = select(select_list, bind=bind)
        # Inject the recursive query, for getting parents
        _dumm, qry = TaxonomyBO._add_recursive_query(qry, tf, do_concat=False)
        qry = qry.where(tf.c.id == any_(taxon_ids))
        # Add another join for getting children
        logger.info("Taxo query: %s with IDs %s", qry, taxon_ids)
        res: ResultProxy = session.execute(qry)
        self.taxa: List[TaxonBO] = []
        for a_rec in res.fetchall():
            lst_rec = list(a_rec)
            cat_type, nbobj1, nbobj2, display_name = lst_rec.pop(0), lst_rec.pop(0), lst_rec.pop(0), lst_rec.pop(0)
            lineage_id = [an_id for an_id in lst_rec[0::2] if an_id]
            lineage = [name for name in lst_rec[1::2] if name]
            #assert lineage_id[-1] in (1, 84960, 84959), "Unexpected root %s" % str(lineage_id[-1])
            self.taxa.append(TaxonBO(cat_type, display_name, nbobj1, nbobj2, lineage, lineage_id))  # type:ignore
        self.get_children(session)
        self.get_cardinalities(session)

    def get_children(self, session: Session):
        # Enrich TaxonBOs with children
        bos_per_id = {a_bo.id: a_bo for a_bo in self.taxa}
        tch = Taxonomy.__table__.alias('tch')
        qry: Query = session.query(Taxonomy.id, tch.c.id)
        qry = qry.join(tch, tch.c.parent_id == Taxonomy.id)
        qry = qry.filter(Taxonomy.id == any_(list(bos_per_id.keys())))
        for an_id, a_child_id in qry.all():
            bos_per_id[an_id].children.append(a_child_id)

    def get_cardinalities(self, session: Session):
        # Enrich TaxonBOs with number of objects. Due to ecotaxa/ecotaxa_dev#648, pick data from projects stats.
        bos_per_id = {a_bo.id: a_bo for a_bo in self.taxa}
        qry: Query = session.query(ProjectTaxoStat.id, func.sum(ProjectTaxoStat.nbr_v))
        qry = qry.filter(ProjectTaxoStat.id == any_(list(bos_per_id.keys())))
        qry = qry.group_by(ProjectTaxoStat.id)
        for an_id, a_sum in qry.all():
            bos_per_id[an_id].nb_objects = a_sum

    def as_list(self) -> List[TaxonBO]:
        return self.taxa


class TaxonBOSetFromWoRMS(object):
    """
        Many taxa from WoRMS table, with lineage.
    """
    MAX_TAXONOMY_LEVELS = 20

    def __init__(self, session: Session, taxon_ids: ClassifIDListT):
        tf = WoRMS.__table__.alias('tf')
        # bind = None  # Uncomment for portable SQL, no 'ilike'
        bind = session.get_bind()
        select_list = [tf.c.aphia_id, tf.c.scientificname]
        select_list.extend([text("t%d.aphia_id, t%d.scientificname" % (level, level))  # type:ignore
                            for level in range(1, TaxonBOSetFromWoRMS.MAX_TAXONOMY_LEVELS)])
        qry = select(select_list, bind=bind)
        # Inject a query on names and hierarchy
        # Produced SQL looks like:
        #       left join worms t1 on tf.parent_name_usage_id=t1.aphia_id
        #       left join worms t2 on t1.parent_name_usage_id=t2.aphia_id
        # ...
        #       left join worms t14 on t13.parent_name_usage_id=t14.aphia_id
        lev_alias = WoRMS.__table__.alias('t1')
        # Chain outer joins on Taxonomy, for parents
        # hook the first OJ to main select
        chained_joins = tf.join(lev_alias,
                                lev_alias.c.aphia_id == tf.c.parent_name_usage_id,
                                isouter=True)
        prev_alias = lev_alias
        for level in range(2, self.MAX_TAXONOMY_LEVELS):
            lev_alias = WoRMS.__table__.alias('t%d' % level)
            # hook each following OJ to previous one
            chained_joins = chained_joins.join(lev_alias,
                                               lev_alias.c.aphia_id == prev_alias.c.parent_name_usage_id,
                                               isouter=True)
            # Collect expressions
            prev_alias = lev_alias
        qry = qry.select_from(chained_joins)
        qry = qry.where(tf.c.aphia_id == any_(taxon_ids))
        logger.info("Taxo query: %s with IDs %s", qry, taxon_ids)
        res: ResultProxy = session.execute(qry)
        self.taxa = []
        for a_rec in res.fetchall():
            lst_rec = list(a_rec)
            lineage_id = [an_id for an_id in lst_rec[0::2] if an_id]
            lineage = [name for name in lst_rec[1::2] if name]
            biota_pos = lineage.index('Biota') + 1
            lineage = lineage[:biota_pos]
            lineage_id = lineage_id[:biota_pos]
            self.taxa.append(TaxonBO('P', lineage[0], 0, 0, lineage, lineage_id))  # type:ignore
        self.get_children(session, self.taxa)

    def get_children(self, session: Session, taxa_list: List[TaxonBO]):
        # Enrich TaxonBOs with children
        bos_per_id = {a_bo.id: a_bo for a_bo in taxa_list}
        tch = WoRMS.__table__.alias('tch')
        qry: Query = session.query(WoRMS.aphia_id, tch.c.aphia_id)
        qry = qry.join(tch, tch.c.parent_name_usage_id == WoRMS.aphia_id)
        qry = qry.filter(WoRMS.aphia_id == any_(list(bos_per_id.keys())))
        for an_id, a_child_id in qry.all():
            bos_per_id[an_id].children.append(a_child_id)

    def as_list(self) -> List[TaxonBO]:
        return self.taxa


class WoRMSSetFromTaxaSet(object):
    """
        Many taxa from WoRMS table, with lineage.
    """

    def __init__(self, session: Session, taxon_ids: ClassifIDListT):
        # Do the matching right away, most strict way
        match = TaxonomyChangeService.strict_match(session, taxon_ids)
        # Format result
        self.res = {}
        for taxo, worms in match:
            self.res[taxo.id] = worms
