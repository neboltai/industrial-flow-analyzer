"""Independent negative protocol tests, including side effects and stderr."""
import contextlib
import importlib.util
import io
import json
import unittest
from unittest.mock import patch
from fixtures import ROOT
from test_mcp_protocol import modern, session

spec = importlib.util.spec_from_file_location('ifa_demo_server', ROOT / 'mcp/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
V = server.META_PROTOCOL_VERSION
C = server.META_CLIENT_CAPABILITIES


def handshake():
    return {'jsonrpc': '2.0', 'id': 10, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-11-25', 'capabilities': {},
        'clientInfo': {'name': 'negative-tests', 'version': '1'}}}


class StrictProtocolTest(unittest.TestCase):
    def test_missing_meta_and_pre_handshake_tools(self):
        for method in ('server/discover', 'tools/list', 'tools/call'):
            with self.subTest(method=method):
                response = server.handle_request({'jsonrpc':'2.0','id':1,'method':method})
                self.assertEqual(response['error']['code'], -32602)

    def test_invalid_modern_metadata_never_falls_back(self):
        invalid = [None, [], False, {}, {V:'2026-07-28'}, {C:{}}]
        invalid += [{V:v,C:{}} for v in (None, 20260728, True, [], {}, '', 'tomorrow')]
        invalid += [{V:'2026-07-28',C:c} for c in (None, [], '', 1, False)]
        for initialized in (False, True):
            state = server.Session()
            if initialized:
                server.handle_request(handshake(),state)
            for method in ('server/discover','tools/list','tools/call'):
                for meta in invalid:
                    if initialized and method != "server/discover" and meta == {}:
                        continue  # Empty legacy metadata carries no modern intent.
                    with self.subTest(initialized=initialized, method=method, meta=meta):
                        request=modern(1,method);request['params']['_meta']=meta
                        self.assertEqual(server.handle_request(request,state)['error']['code'],-32602)

    def test_unsupported_well_formed_version(self):
        request=modern(1,'tools/list');request['params']['_meta'][V]='1900-01-01'
        response=server.handle_request(request)
        self.assertEqual(response['error']['code'],-32022)
        self.assertIn('2026-07-28',response['error']['data']['supported'])

    def test_invalid_request_ids(self):
        for identifier in (None, True, False, 1.5, [], {}):
            request=modern(1,'tools/list');request['id']=identifier
            response=server.handle_request(request)
            self.assertEqual(response['error']['code'],-32600)
            self.assertIsNone(response['id'])

    def test_notifications_have_no_response_or_handler_side_effects(self):
        state=server.Session()
        with patch.dict(server.HANDLERS, {'list_demo_datasets':lambda args:self.fail('notification executed handler')}):
            for request in (modern(1,'tools/list'),modern(1,'tools/call',{'name':'list_demo_datasets'}),handshake(), {'method':'notifications/initialized','jsonrpc':'2.0'}):
                request.pop('id',None)
                self.assertIsNone(server.handle_request(request,state))
        self.assertFalse(state.initialized)

    def test_stateless_modern_requests_after_legacy(self):
        request=modern(1,'tools/list')
        state=server.Session();server.handle_request(handshake(),state)
        self.assertEqual(server.handle_request(request,state),server.handle_request(request))
        self.assertIn('result',server.handle_request({'jsonrpc':'2.0','id':2,'method':'tools/list'},state))

    def test_invalid_handshake_cannot_initialize(self):
        for field in ('protocolVersion','capabilities','clientInfo'):
            state=server.Session();request=handshake();del request['params'][field]
            self.assertEqual(server.handle_request(request,state)['error']['code'],-32602)
            self.assertFalse(state.initialized)

    def test_internal_error_redacted_and_logged(self):
        def fail(args):
            raise RuntimeError('synthetic internal detail')
        err=io.StringIO();out=io.StringIO()
        with patch.dict(server.HANDLERS,{'list_demo_datasets':fail}),contextlib.redirect_stderr(err),contextlib.redirect_stdout(out):
            result=server.handle_request(modern(1,'tools/call',{'name':'list_demo_datasets'}))
        self.assertTrue(result['result']['isError'])
        self.assertNotIn('synthetic internal detail',json.dumps(result))
        self.assertIn('synthetic internal detail',err.getvalue())
        self.assertIn('Traceback',err.getvalue())
        self.assertEqual(out.getvalue(),'')

    def test_nested_moves_are_checked_against_declared_schema(self):
        from jsonschema import Draft202012Validator
        name='simulate_demo_moves'
        schema=next(t['inputSchema'] for t in server.TOOLS if t['name']==name)
        Draft202012Validator.check_schema(schema)
        valid={'type':'pair_swap','sku_a':'SKU-A','sku_b':'SKU-B'}
        cases=[valid,{},None,[],1,'swap',dict(valid,extra=True),dict(valid,type='move'),dict(valid,sku_a=3),dict(valid,sku_b=False)]
        for key in valid:
            case=dict(valid);del case[key];cases.append(case)
        for move in cases:
            with self.subTest(move=move):
                args={'dataset_id':'fictional-small-warehouse','moves':[move]}
                expected=Draft202012Validator(schema).is_valid(args)
                if expected:
                    server._validate_tool_arguments(name,args)
                else:
                    with self.assertRaises(server.ToolError):server._validate_tool_arguments(name,args)
                    with patch.dict(server.HANDLERS,{name:lambda a:self.fail('invalid args reached handler')}):
                        result=server.handle_request(modern(1,'tools/call',{'name':name,'arguments':args}))
                        self.assertTrue(result['result']['isError'])

    def test_stdio_contains_only_correlated_responses(self):
        notification={'jsonrpc':'2.0','method':'tools/list','params':modern(1,'tools/list')['params']}
        replies=session([notification,modern(7,'tools/list')])
        self.assertEqual(set(replies),{7})


class HistoricalParityTest(unittest.TestCase):
    def test_six_historical_results_remain_identical(self):
        args={'dataset_id':'fictional-small-warehouse'}
        analysis=server.tool_analyze_demo_flows(args)
        metrics=analysis['metrics']
        self.assertEqual(metrics['total_valid_orders'],15)
        self.assertEqual(analysis['data_quality']['route_coverage_percent'],100.0)
        self.assertEqual(metrics['total_distance_m'],1560.0)
        self.assertEqual(metrics['median_distance_per_order_m'],100.0)
        self.assertEqual(metrics['p90_distance_per_order_m'],120.0)
        recommendations=server.tool_recommend_demo_slotting(args)['recommendations']
        self.assertEqual(len(recommendations),1)
        self.assertEqual(recommendations[0]['recommendation_id'],'SWAP-001')
        self.assertEqual(recommendations[0]['estimated_reduction_m'],80.0)
        self.assertEqual(recommendations[0]['estimated_reduction_percent'],5.1282)
