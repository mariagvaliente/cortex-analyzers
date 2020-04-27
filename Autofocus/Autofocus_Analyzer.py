#!/usr/bin/env python3
# encoding: utf-8
from autofocus import AutoFocusAPI, AFSample, AFServerError, AFClientError, AFSampleAbsent
from cortexutils.analyzer import Analyzer
import requests
import json
from datetime import datetime
import matplotlib.pyplot as plt
import networkx as nx
import pathlib
import os
import tempfile
from shutil import copyfileobj
import ntpath

AutoFocusAPI.api_key = "Your API key here"

# Main analyzer
class AutoFocusAnalyzer(Analyzer):
    def __init__(self):
        Analyzer.__init__(self)
        self.service = self.getParam(
            'config.service', None, 'Service parameter is missing')
        self.autofocus_key = self.getParam(
            'config.apikey', None, 'Missing AutoFocus API key')
        #URL api only for data types: IP, domain and url
        self.basic_url = "https://autofocus.paloaltonetworks.com/api/v1.0/tic"
        if self.service == "search_ioc":
            self.headers = {"apiKey": self.autofocus_key, "Content-Type": "application/json"}
        else:
            self.headers = {"Content-Type": "application/json"}
            self.data = {"apiKey": self.autofocus_key, "coverage": "true", "sections": ["coverage"]}

    def get_request(self):
        indicator_type_initial = str(self.data_type)
        if indicator_type_initial == "ip":
           indicator_type = "ipv4_address"
        elif indicator_type_initial == "domain":
           indicator_type = "domain"
        elif indicator_type_initial == "url":
           indicator_type = "url"
        indicator_value = str(self.getData())
        self.params = {"indicatorType": indicator_type, "indicatorValue": indicator_value, "includeTags": "true"}
        url = str(self.basic_url)
        r = requests.get(url, params=self.params, headers=self.headers)
        res_search = r.json()
        indicator = res_search.get('indicator')
        tags = res_search.get('tags')
        res = {'metadata': indicator, 'tags': tags}
        return res

    def get_analysis(self):
        indicator_value = str(self.getData())
        url_analysis = "https://autofocus.paloaltonetworks.com/api/v1.0/sample/"
        query = "/analysis"
        url = url_analysis + indicator_value + query
        data = json.dumps(self.data)
        r = requests.post(url, data=data, headers=self.headers)
        res_search = r.json()
        return res_search

    def execute_autofocus_service(self):
        data = self.getData()
        AutoFocusAPI.api_key = self.autofocus_key
        sample = AFSample.get(data)
        analysis = self.get_analysis()
        res = {'metadata': sample.serialize(),'tags': [tag.serialize() for tag in sample.__getattribute__('tags')], 'analysis': analysis}
        return res

    def summary(self, raw):
        taxonomies = []
        level = "info"
        namespace = "PaloAltoNetworks"
        value = "1"

        if "metadata" in raw:
            if self.service == "search_hash":
                verdict = raw.get('metadata').get('verdict')
                last_seen = raw.get('metadata').get('finish_date')
            else:
                verdict_dict = raw.get('metadata').get('latestPanVerdicts')
                if verdict_dict.get('WF_SAMPLE') != None:
                    verdict = verdict_dict.get('WF_SAMPLE')
                elif verdict_dict.get('PAN_DB') != None:
                    verdict = verdict_dict.get('PAN_DB')
                else:
                    verdict = None
                last_seen_timestamp = raw.get('metadata').get('lastSeenTsGlobal')
                if last_seen_timestamp != None:
                    last_seen_timestamp_str = str(last_seen_timestamp)
                    last_seen_timestamp_cut = last_seen_timestamp_str[:-3]
                    last_seen_timestamp_result = int(last_seen_timestamp_cut)
                    last_seen = datetime.fromtimestamp(last_seen_timestamp_result).isoformat()
                else:
                    last_seen = "Not found"
            if verdict == "benign" or verdict == "BENIGN":
                value = "0"
                level = "safe"
            elif verdict == "greyware" or verdict == "GREYWARE":
                value = "3"
                level = "suspicious"
            elif verdict == "phising" or verdict == "PHISING":
                value = "4"
                level = "malicious"
            elif verdict == "malware" or verdict == "MALWARE" or verdict == "C2":
                value = "5"
                level = "malicious"
            taxonomies.append(self.build_taxonomy(level,namespace,"Score",value))
            taxonomies.append(self.build_taxonomy(level,namespace,"Last_seen",last_seen))
        else:
            value = "Not found"
            taxonomies.append(self.build_taxonomy(level,namespace,"Autofocus",value))
        return {'taxonomies': taxonomies}

    def createArtifactFile(self, path):
        (dst, filename) = tempfile.mkstemp(dir=os.path.join(self.job_directory, "output"))
        try:
            with open(path, 'r') as src:
                copyfileobj(src, os.fdopen(dst, 'w'))
                return {'dataType': "file", 'file': ntpath.basename(filename),  'filename': ntpath.basename(path)}
        except UnicodeDecodeError:
            with open(path, 'rb') as src:
                copyfileobj(src, os.fdopen(dst, 'wb'))
                return {'dataType': "file", 'file': ntpath.basename(filename),  'filename': ntpath.basename(path)}

    def artifacts(self, report):
        artifacts = []
        relations = []
        tags = report.get('tags')
        src = str(self.data_type)
        if len(tags) != 0:
           for tag in tags:
               if self.service == "search_hash":
                  tag_name = tag.get('name')
               else:
                  tag_name = tag.get('tag_name')

               tag_class_id = tag.get('tag_class_id')
               if tag_class_id == 1:
                  dst = 'threat_actor'
                  observable = {'dataType': 'threat_actor', 'data': tag_name}
               elif tag_class_id == 2:
                  dst = 'campaign'
                  observable = {'dataType': 'campaign', 'data': tag_name}
               elif tag_class_id == 3:
                  dst = 'malware_family'
                  observable = {'dataType': 'malware_family', 'data': tag_name}
               elif tag_class_id == 4:
                  if tag_name.find("CVE") >= 0:
                     dst = 'vulnerability'
                     observable = {'dataType': 'vulnerability', 'data': tag_name}
                  else:
                     dst = 'exploit'
                     observable = {'dataType': 'exploit', 'data': tag_name}
               else:
                  dst = 'attack_pattern'
                  observable = {'dataType': 'attack_pattern', 'data': tag_name}
               artifacts.append(observable)
               relations.append(dst)

        if self.service == "search_hash":
            analysis = report.get('analysis')
            if analysis != None:
                coverage = analysis.get('coverage')
                if coverage != None:
                    malware_sig = coverage.get('wf_av_sig')
                    dns_sig = coverage.get('dns_sig')
                    fileurl_sig = coverage.get('fileurl_sig')
                    url_cat = coverage.get('url_cat')
                    if len(malware_sig) != 0:
                        for sig in malware_sig:
                            sig_name = sig.get('name')
                            dst = 'malware_family'
                            observable_sig = {'dataType': 'malware_family', 'data': sig_name}
                            artifacts.append(observable_sig)
                    if len(dns_sig) != 0:
                        for domain in dns_sig:
                            dns_name = domain.get('domain')
                            dst = 'domain'
                            observable_dns = {'dataType': 'domain', 'data': dns_name}
                            artifacts.append(observable_dns)
                    if len(url_cat) != 0:
                        for url in url_cat:
                            url_name = url.get('url')
                            dst = 'url'
                            observable_url = {'dataType': 'url', 'data': url_name}
                            artifacts.append(observable_url)
                    relations.append(dst)
        observables = {'src': src, 'dst': relations}

        G = nx.DiGraph()
        #Anyadir atributos al nodo src
        G.add_node(observables['src'])

        #Anyadir atributos al nodo dst

        obs_dst = observables['dst']
        for o in obs_dst:
            #Anyadir atributos a las aristas
            G.add_edge(observables['src'], o)

       #Crear fichero con el subgrafo
        nx.write_gml(G, "/tmp/subgraph.gml")
        path = "/tmp/subgraph.gml"
        observable_subgraph = self.createArtifactFile(path)
        artifacts.append(observable_subgraph)

        return artifacts


    def run(self):
        try:
            if self.service == "search_hash":
                records = self.execute_autofocus_service()
            else:
                records = self.get_request()

            self.report(records)

        except AFSampleAbsent as e: # Sample not in Autofocus
            self.error('Unknown sample in Autofocus')
        except AFServerError as e: # Server error
            self.unexpectedError(e)
        except AFClientError as e: # Client error
            self.unexpectedError(e)
        except Exception: # Unknown error
            self.unexpectedError("Unknown error while running Autofocus analyzer")

if __name__ == '__main__':
    AutoFocusAnalyzer().run()