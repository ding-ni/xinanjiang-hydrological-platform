"""Saved control hydrographs stay queryable when split routing cannot resume."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import write_json
from diagnostic_jobs import point_source, require_point_rerun, sha


class RiverQuerySourceTests(unittest.TestCase):
    def test_complete_continuation_evidence_can_be_read_without_migrating_its_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'tasks';folder=root/'JOB-continued';folder.mkdir(parents=True)
            request={'runType':'stateContinuation','period':{'initialStateVersionId':'STATE-parent'},'lineage':{}}
            write_json(folder/'request.json',request)
            (folder/'routing-runoff.npz').write_bytes(b'preserved-runoff')
            metadata={'taskId':'JOB-continued','lineage':{},'initialRoutingState':{'prev_out':[3.0]},'arraySHA256':sha(folder/'routing-runoff.npz')}
            write_json(folder/'routing-evidence.json',metadata)
            result={'routingEvidence':{'arraySHA256':metadata['arraySHA256'],'catalogSHA256':sha(folder/'routing-evidence.json')}}
            class Source:
                def __init__(self):self.root=root
                def result(self,_identifier):return result
            _folder,source,_result,evidence=point_source(Source(),'JOB-continued')
            self.assertEqual(source,request)
            self.assertEqual(evidence['initialRoutingState'],{'prev_out':[3.0]})
            with self.assertRaisesRegex(ValueError,'河道中间断面需独立复算'):
                require_point_rerun(source,evidence,Path(temporary))

    def test_rerouting_still_checks_implementation_and_never_reuses_routing_memory(self):
        with patch('diagnostic_jobs.freeze_science_selection') as freeze:
            require_point_rerun({'runType':'simulation','period':{},'science':{'versionId':'SCIENCE-one'}},{'initialRoutingState':None},Path('runtime'))
            freeze.assert_called_once()
            for request,evidence in [({'period':{'initialStateVersionId':'STATE-one'}},{'initialRoutingState':None}),({'period':{}},{'initialRoutingState':{'prev_out':[1]}})]:
                with self.assertRaisesRegex(ValueError,'控制断面可直接查询'):
                    require_point_rerun(request,evidence,Path('runtime'))


if __name__=='__main__':unittest.main()
