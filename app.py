"""app.py

Wrapping an app that links named entities (as recognized by spaCy)
with its wikidata's information (id, label, description, url).

Usage:

$ python app.py -t example-mmif-nel.json out-nel.json
$ python app.py [--develop]

The first invocation is to just test the app without running a server. The
second is to start a server, which you can ping with

$ curl -H "Accept: application/json" -X POST -d@example-mmif-nel.json http://0.0.0.0:5000/

With the --develop option you get a FLask server running in development mode,
without it Gunicorn will be used for a more stable server.

Normally you would run this in a Docker container, see README.md.

"""

import os
import sys
import collections
import json
import urllib
import argparse

import pywikibot

from pywikibot.data import api
from clams.app import ClamsApp
from clams.restify import Restifier
from clams.appmetadata import AppMetadata
from mmif.serialize import Mmif
from mmif.vocabulary import AnnotationTypes, DocumentTypes
from lapps.discriminators import Uri

# Define Uri_NEL and Uri_DEP since it is not on lappsgrid.org yet
Uri_NEL = "http://vocab.lappsgrid.org/LinkedNamedEntity"
Uri_DEP = "http://vocab.lappsgrid.org/SyntacticRelation"
Uri_NELR = "http://vocab.lappsgrid.org/LinkedNamedEntityRelation"

APP_VERSION = '0.0.8'
APP_LICENSE = 'Apache 2.0'
MMIF_VERSION = '0.4.0'
MMIF_PYTHON_VERSION = '0.4.6'
CLAMS_PYTHON_VERSION = '0.5.1'
SPACY_VERSION = '3.1.2'
SPACY_LICENSE = 'MIT'

# We need this to find the text documents in the documents list
TEXT_DOCUMENT = os.path.basename(str(DocumentTypes.TextDocument))

DEBUG = False


class NEL_App(ClamsApp):

    def _appmetadata(self):
        metadata = AppMetadata(
            identifier='https://apps.clams.ai/named_entity_linking',
            url='https://github.com/JinnyViboonlarp/app-nel',
            name="NEL with Wikidata",
            description="Link all named entities in an MMIF file with its wikidata's information.",
            app_version=APP_VERSION,
            app_license=APP_LICENSE,
            analyzer_version=SPACY_VERSION,
            analyzer_license=SPACY_LICENSE,
            mmif_version=MMIF_VERSION
        )

        metadata.add_input(Uri.NE)
        metadata.add_output(Uri_NEL)
        metadata.add_output(Uri_NELR)
        return metadata

    def _annotate(self, mmif, **kwargs):
        Identifiers.reset()
        self.mmif = mmif if type(mmif) is Mmif else Mmif(mmif)
        for view in list(self.mmif.views):
            # check if the app is spacy_nlp with NamedEntity component
            components = [os.path.basename(str(component)) for component in view['metadata']['contains']]
            if(('spacy_nlp' in str(view['metadata']['app'])) and ('NamedEntity' in components)):
                doc_id = None
                for component in view['metadata']['contains']:
                    if(('NamedEntity' == os.path.basename(str(component))) and ("document" in view['metadata']['contains'][component])):
                        # check if there is a document's id (of the text document that the NER is performed on) in the view's metadata
                        # if the view's metadata does not contain this info, it means that the doc_id is in "properties" field of each annotation in the view
                        doc_id = view['metadata']['contains'][component]["document"]
                        doc_id = view.id + ':' + doc_id
                if(doc_id == None):
                    new_view = self._new_view()
                    self._add_tool_output(view, new_view, view.id)
                else:
                    new_view = self._new_view(doc_id)
                    self._add_tool_output(view, new_view)
                    
        return self.mmif

    def _new_view(self, docid=None):
        view = self.mmif.new_view()
        self.sign_view(view)
        for attype in (Uri_NEL, Uri_NELR):
            view.new_contain(attype, document=docid)
        return view

    def _add_tool_output(self, old_view, new_view, view_id=None):

        # This method is for querying wikidata
        # Currently, only the first (most likely) search result is returned
        def getItems(site, itemtitle):
            params = { 'action' :'wbsearchentities' , 'format' : 'json' , 'language' : 'en', 'type' : 'item', 'search': itemtitle, 'limit': 1}
            request = api.Request(site=site,**params)
            return request.submit()

        # Login to wikidata
        site = pywikibot.Site("wikidata", "wikidata")
        repo = site.data_repository()

        # for each NER-type annotation, use the text to search wikidata
        interested_entities = collections.defaultdict(dict) # this is a dict to store information of entities with wikidata information
        interested_keys = ['url','label','description']
        annotation_property_order = ['root_i','text','label','category','description','wikidata_id','url','id']
        for annotation in old_view['annotations']:
            if(annotation.at_type == Uri.NE):
                doc_id = annotation['properties']['document'] if "document" in annotation['properties'] else None
                if((view_id != None) and (doc_id != None)):
                    doc_id = view_id + ':' + doc_id
                wikidataEntries = getItems(site, annotation['properties']['text'])
                firstEntry = wikidataEntries["search"][0] if (len(wikidataEntries["search"])>0) else None
                if(firstEntry != None):
                    # create new annotation from the old annotation plus the data from wikidata
                    properties = { "text": annotation['properties']['text'], "category": annotation['properties']['category'], \
                                   "root_i": annotation['properties']['root_i']}
                    if 'id' in firstEntry:
                        properties['wikidata_id'] = firstEntry['id']
                    for key in interested_keys:
                        if key in firstEntry:
                            properties[key] = firstEntry[key]
                    properties = {key: properties[key] for key in annotation_property_order if key in properties}
                    add_annotation(
                        new_view, Uri_NEL, Identifiers.new("nel"),
                        doc_id, annotation['properties']['start'], annotation['properties']['end'],
                        properties)
                    # store doc_id and root_i in a dict to search later for meaningful relations between named entities with
                    # wikidata entries
                    doc_id = annotation['properties']['document'] if "document" in annotation['properties'] else 0
                    entity_id = annotation['properties']['root_i']
                    interested_entities[doc_id][entity_id] = properties
                    
        # store syntactic relations in the old view (so that they could be searched later
        child_to_head = collections.defaultdict(dict)
        for annotation in old_view['annotations']:
            if(annotation.at_type == Uri_DEP):
                doc_id = annotation['properties']['document'] if "document" in annotation['properties'] else 0
                child_i = annotation['properties']['child_i']
                child_to_head[doc_id][child_i] = {key: annotation['properties'][key] for key in ['dep','head_text','head_lemma','head_i']}

        # search for named entities with the same head texts
        for doc_id in interested_entities:
            for i1 in interested_entities[doc_id]: # iterate over root_i of named entities in this document
                for i2 in interested_entities[doc_id]:
                    if ((i1 < i2) and (i1 in child_to_head[doc_id]) and (i2 in child_to_head[doc_id]) and \
                        (child_to_head[doc_id][i1]["head_i"] == child_to_head[doc_id][i2]["head_i"])):
                        properties = { "e1_text": interested_entities[doc_id][i1]['text'], "e1_label": interested_entities[doc_id][i1]['label'], \
                                    "e1_root_i": interested_entities[doc_id][i1]['root_i'], "e1_wikidata_id": interested_entities[doc_id][i1]['wikidata_id'], \
                                    "e1_dep": child_to_head[doc_id][i1]['dep'], \
                                        "e2_text": interested_entities[doc_id][i2]['text'], "e2_label": interested_entities[doc_id][i2]['label'], \
                                    "e2_root_i": interested_entities[doc_id][i2]['root_i'], "e2_wikidata_id": interested_entities[doc_id][i2]['wikidata_id'], \
                                    "e2_dep": child_to_head[doc_id][i2]['dep'], \
                                        "rel_text": child_to_head[doc_id][i1]['head_text'], "rel_lemma": child_to_head[doc_id][i1]['head_lemma'], \
                                    "rel_i": child_to_head[doc_id][i1]['head_i']}
                        doc_id_for_annotation = doc_id if (doc_id != 0) else None
                        if((view_id != None) and (doc_id_for_annotation != None)):
                            doc_id_for_annotation = view_id + ':' + doc_id_for_annotation
                        add_annotation(
                            new_view, Uri_NELR, Identifiers.new("nelr"),
                            doc_id_for_annotation, None, None,
                            properties)
        

def add_annotation(view, attype, identifier, doc_id, start, end, properties):
    """Utility method to add an annotation to a view."""
    a = view.new_annotation(attype, identifier)
    if doc_id is not None:
        a.add_property('document', doc_id)
    if start is not None:
        a.add_property('start', start)
    if end is not None:
        a.add_property('end', end)
    for prop, val in properties.items():
        a.add_property(prop, val)


class Identifiers(object):

    """Utility class to generate annotation identifiers. You could, but don't have
    to, reset this each time you start a new view. This works only for new views
    since it does not check for identifiers of annotations already in the list
    of annotations."""

    identifiers = collections.defaultdict(int)

    @classmethod
    def new(cls, prefix):
        cls.identifiers[prefix] += 1
        return "%s%d" % (prefix, cls.identifiers[prefix])

    @classmethod
    def reset(cls):
        cls.identifiers = collections.defaultdict(int)



def test(infile, outfile):
    """Run named entity linking on an input MMIF file. This bypasses the server and just pings
    the annotate() method on the NEL_App class. Prints a summary of the views
    in the end result."""
    print(NEL_App().appmetadata(pretty=True))
    with open(infile) as fh_in, open(outfile, 'w') as fh_out:
        mmif_out_as_string = NEL_App().annotate(fh_in.read(), pretty=True)
        mmif_out = Mmif(mmif_out_as_string)
        fh_out.write(mmif_out_as_string)
        for view in mmif_out.views:
            print("<View id=%s annotations=%s app=%s>"
                  % (view.id, len(view.annotations), view.metadata['app']))


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--test',  action='store_true', help="bypass the server")
    parser.add_argument('--develop',  action='store_true', help="start a development server")
    parser.add_argument('infile', nargs='?', help="input MMIF file")
    parser.add_argument('outfile', nargs='?', help="output file")
    args = parser.parse_args()

    if args.test:
        test(args.infile, args.outfile)
    else:
        app = NEL_App()
        service = Restifier(app)
        if args.develop:
            service.run()
        else:
            service.serve_production()
