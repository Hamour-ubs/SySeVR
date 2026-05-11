# -*- coding: utf-8 -*-
import os
import pickle
import shutil
import tempfile
from contextlib import contextmanager

from joern.all import JoernSteps

from access_db_operate import (getFuncNodeInTestID, getCallGraph,
                               getUseDefVarByPDG, translateCFGByNode,
                               translatePDGByNode)
from complete_PDG import (addDataEdgeOfObject, completeDataEdgeOfPDG,
                          completeDeclStmtOfPDG, modifyDataEdgeVal,
                          modifyStmtNode)
from general_op import sortedNodesByLoc
from get_cfg_relation import completeDataEdgeOfCFG, getCtrlRealtionOfCFG
from slice_op import (process_cross_func,
                      process_crossfuncs_back_byfirstnode,
                      program_slice_backwards)


class SnippetSlicer(object):
    def __init__(self, joern_db_url=None, workspace=None, keep_workspace=False):
        self.keep_workspace = keep_workspace
        self.workspace = workspace or tempfile.mkdtemp(prefix='snippet_slice_')
        self.joern = JoernSteps()
        if joern_db_url:
            self.joern.setGraphDbURL(joern_db_url)
        self.joern.connectToDatabase()
        self._ensure_workspace_dirs()

    def _ensure_workspace_dirs(self):
        for dirname in ['pdg_db', 'dict_call2cfgNodeID_funcID', 'source']:
            path = os.path.join(self.workspace, dirname)
            if not os.path.exists(path):
                os.makedirs(path)

    def cleanup(self):
        if self.keep_workspace:
            return
        if os.path.exists(self.workspace):
            shutil.rmtree(self.workspace)

    @contextmanager
    def _workdir(self):
        current = os.getcwd()
        os.chdir(self.workspace)
        try:
            yield
        finally:
            os.chdir(current)

    def _write_snippet(self, code, filename, test_id):
        source_dir = os.path.join(self.workspace, 'source', test_id)
        if not os.path.exists(source_dir):
            os.makedirs(source_dir)
        filepath = os.path.join(source_dir, filename)
        with open(filepath, 'w') as fout:
            fout.write(code)
            if not code.endswith('\n'):
                fout.write('\n')
        return filepath

    def _build_cfg(self, func_node):
        init_cfg = translateCFGByNode(self.joern, func_node)
        opt_cfg = modifyStmtNode(init_cfg)
        cfg = completeDataEdgeOfCFG(opt_cfg)
        dict_if2cfgnode = getCtrlRealtionOfCFG(cfg)

        dict_cfgnode2if = {}
        for key in dict_if2cfgnode.keys():
            list_nodes = dict_if2cfgnode[key][0] + dict_if2cfgnode[key][1]
            for node_name in list_nodes:
                if node_name not in dict_cfgnode2if:
                    dict_cfgnode2if[node_name] = [key]
                else:
                    dict_cfgnode2if[node_name].append(key)

        for key in dict_cfgnode2if.keys():
            dict_cfgnode2if[key] = list(set(dict_cfgnode2if[key]))

        return cfg, dict_if2cfgnode, dict_cfgnode2if

    def _parse_line(self, location):
        if location is None or ':' not in location:
            return None
        try:
            return int(location.split(':')[0])
        except ValueError:
            return None

    def _build_pdg(self, func_node, cfg, dict_if2cfgnode, dict_cfgnode2if):
        init_pdg = translatePDGByNode(self.joern, func_node)
        opt_pdg = modifyStmtNode(init_pdg)

        cfg_names = set(cfg.vs['name'])
        for vertex in opt_pdg.vs:
            if vertex['type'] == 'Statement' and vertex['name'] not in cfg_names:
                vertex_line = self._parse_line(vertex['location'])
                if vertex_line is None:
                    continue
                for n in cfg.vs:
                    cfg_line = self._parse_line(n['location'])
                    if cfg_line is None:
                        continue
                    if vertex['code'] == n['code'] and vertex_line == cfg_line:
                        vertex['name'] = n['name']
                        vertex['location'] = n['location']
                        break

        d_use, d_def = getUseDefVarByPDG(self.joern, opt_pdg)
        opt_pdg = modifyDataEdgeVal(opt_pdg)
        opt_pdg = completeDeclStmtOfPDG(opt_pdg, d_use, d_def, dict_if2cfgnode, dict_cfgnode2if)
        opt_pdg = completeDataEdgeOfPDG(opt_pdg, d_use, d_def, dict_if2cfgnode, dict_cfgnode2if)
        opt_pdg = addDataEdgeOfObject(opt_pdg, dict_if2cfgnode, dict_cfgnode2if)

        return opt_pdg

    def _store_pdg(self, pdg, func_node, test_id):
        pdg_dir = os.path.join(self.workspace, 'pdg_db', test_id)
        if not os.path.exists(pdg_dir):
            os.makedirs(pdg_dir)
        store_file_name = func_node.properties['name'] + '_' + str(func_node._id)
        store_path = os.path.join(pdg_dir, store_file_name)
        with open(store_path, 'wb') as fout:
            pickle.dump(pdg, fout, True)

    def _build_call_dict(self, test_id):
        call_g = getCallGraph(self.joern, test_id)
        if call_g is False:
            return False
        call_dir = os.path.join(self.workspace, 'dict_call2cfgNodeID_funcID', test_id)
        if not os.path.exists(call_dir):
            os.makedirs(call_dir)
        call_map = {}
        for edge in call_g.es:
            endnode = call_g.vs[edge.tuple[1]]
            if endnode['name'] not in call_map:
                call_map[endnode['name']] = [(edge['var'], call_g.vs[edge.tuple[0]]['name'])]
            else:
                call_map[endnode['name']].append((edge['var'], call_g.vs[edge.tuple[0]]['name']))
        with open(os.path.join(call_dir, 'dict.pkl'), 'wb') as fout:
            pickle.dump(call_map, fout, True)
        return True

    def _interprocedural_backwards(self, pdg, startnodes_id, test_id):
        list_startnodes = []
        for node in pdg.vs:
            if node['name'] in startnodes_id:
                list_startnodes.append(node)

        if not list_startnodes:
            return []

        results_back = program_slice_backwards(pdg, list_startnodes)
        results_back = sortedNodesByLoc(results_back)
        start_list = [[results_back, 0]]
        skipped_func_list = []
        list_cross_func_back, skipped_func_list = process_crossfuncs_back_byfirstnode(
            start_list, test_id, 0, skipped_func_list)
        list_results_back = [item[0] for item in list_cross_func_back]

        all_result = []
        for result in list_results_back:
            result, skipped_func_list = process_cross_func(result, test_id, 0, result, skipped_func_list)
            all_result.append(result)
        return all_result

    def _render_slice(self, list_nodes):
        by_file = {}
        for node in list_nodes:
            if node['location'] is None or node['filepath'] is None:
                continue
            line_num = self._parse_line(node['location'])
            if line_num is None:
                continue
            if node['filepath'] not in by_file:
                by_file[node['filepath']] = {line_num}
            else:
                by_file[node['filepath']].add(line_num)

        rendered = []
        for filepath in sorted(by_file.keys()):
            with open(filepath, 'r') as fin:
                content = fin.readlines()
            for line_num in sorted(by_file[filepath]):
                if line_num <= 0 or line_num > len(content):
                    continue
                rendered.append({
                    'file': filepath,
                    'line': line_num,
                    'code': content[line_num - 1].rstrip('\n')
                })

        return rendered

    def slice_snippet(self, code, vuln_line, filename='snippet.c', test_id='snippet'):
        filepath = self._write_snippet(code, filename, test_id)
        return self.slice_file(filepath, vuln_line, test_id)

    def slice_file(self, filepath, vuln_line, test_id=None):
        if test_id is None:
            test_id = os.path.basename(os.path.dirname(filepath))
        func_nodes = getFuncNodeInTestID(self.joern, test_id)
        if not func_nodes:
            raise RuntimeError('No functions found for test ID %s. Ensure the snippet is parsed into Joern.' % test_id)

        pdg_by_func = {}
        for func_node in func_nodes:
            cfg, dict_if2cfgnode, dict_cfgnode2if = self._build_cfg(func_node)
            pdg = self._build_pdg(func_node, cfg, dict_if2cfgnode, dict_cfgnode2if)
            self._store_pdg(pdg, func_node, test_id)
            pdg_by_func[func_node._id] = pdg

        with self._workdir():
            self._build_call_dict(test_id)

            slice_results = []
            for pdg in pdg_by_func.values():
                start_nodes = []
                for node in pdg.vs:
                    if node['filepath'] != filepath or node['location'] is None:
                        continue
                    node_line = self._parse_line(node['location'])
                    if node_line is None:
                        continue
                    if int(node_line) == int(vuln_line):
                        start_nodes.append(node['name'])

                if not start_nodes:
                    continue

                list_slices = self._interprocedural_backwards(pdg, start_nodes, test_id)
                for list_nodes in list_slices:
                    slice_results.append(self._render_slice(list_nodes))

        if not slice_results:
            raise RuntimeError('No slice nodes found for line %s in %s.' % (vuln_line, filepath))

        return slice_results


def _format_slice_output(slice_results):
    lines = []
    for index, slice_result in enumerate(slice_results):
        lines.append('------------------------------')
        lines.append('Slice %d' % (index + 1))
        for item in slice_result:
            lines.append('%s:%d %s' % (item['file'], item['line'], item['code']))
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--code-file', required=True, help='Path to the code snippet file')
    parser.add_argument('--vuln-line', required=True, type=int, help='1-based vulnerable line number')
    parser.add_argument('--test-id', default=None, help='Identifier used to locate functions in Joern')
    parser.add_argument('--joern-db-url', default=None, help='Joern Neo4j DB URL, defaults to localhost')
    parser.add_argument('--workspace', default=None, help='Workspace directory for PDG outputs')
    parser.add_argument('--keep-workspace', action='store_true', help='Do not delete workspace after slicing')
    args = parser.parse_args()

    slicer = SnippetSlicer(joern_db_url=args.joern_db_url,
                           workspace=args.workspace,
                           keep_workspace=args.keep_workspace)
    try:
        results = slicer.slice_file(args.code_file, args.vuln_line, args.test_id)
        print(_format_slice_output(results))
    finally:
        slicer.cleanup()
