import unittest
from resolution_baseline_report import resource_roles, resource_summary


class ResourceReportTests(unittest.TestCase):
    def test_scene_role_does_not_alias_an_equal_offset_in_main_memory(self):
        route=dict(color='0x1000',color_location=0,zeta='0x2000',buffer=0)
        surface=dict(target=2,color=dict(location=1,offset=4096),zeta=[1,8192,4096])
        evidence=dict(displays={'0':dict(location=0,offset=12288)},frames={'one':dict(surfaces=[surface],qualified_resolves=[route])})
        roles=resource_roles(evidence,dict(resources=[]))
        self.assertEqual([r['role'] for r in roles],['display_resolve'])
    def test_depth_only_role_requires_an_actual_consumer_not_matching_dimensions(self):
        surface=dict(target=0,zeta=[0,4096,4096],backing=[2048,2048])
        evidence=dict(frames={'one':dict(surfaces=[surface],qualified_resolves=[])})
        self.assertEqual(resource_roles(evidence,dict(resources=[])),[])
        material=dict(resources=[dict(operation='depth_gpu_alias',location=0,offset='0x1000',calls=1)])
        self.assertEqual(resource_roles(evidence,material)[0]['role'],'sampled_depth_only')
        material['resources'][0]['location']=1
        self.assertEqual(resource_roles(evidence,material),[])
    def test_actual_cpu_deltas_and_memory_peaks(self):
        def row(t,cpu,rss):
            return dict(resources=dict(status='available',monotonic_ns=t,process_cpu_ns=cpu,
                working_set_bytes=rss,private_bytes=rss*2,
                threads=[dict(tid=10,name='ppu main',cpu_ns=cpu//2)]))
        result=resource_summary([row(1_000_000_000,10_000_000,100),row(3_000_000_000,50_000_000,200)])
        self.assertEqual(result['process_cpu_ms'],40)
        self.assertEqual(result['threads'][0]['cpu_ms'],20)
        self.assertEqual(result['peak_working_set_bytes'],200)
        self.assertEqual(result['process_logical_cores'],.02)

    def test_unavailable_resources_are_not_reported_as_zero(self):
        self.assertEqual(resource_summary([])['status'],'unavailable')


if __name__=='__main__':unittest.main()
