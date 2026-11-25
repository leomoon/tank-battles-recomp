import os
import time
import unittest


@unittest.skipUnless(os.name=='nt','Windows process counters')
class WindowsResourceTests(unittest.TestCase):
    def test_all_thread_accounting_includes_unnamed_live_threads(self):
        import threading
        from windows_resources import WindowsResources
        ready,stop=threading.Event(),threading.Event()
        def worker():
            ready.set()
            stop.wait()
        thread=threading.Thread(target=worker);thread.start();ready.wait()
        sampler=WindowsResources(os.getpid(),all_threads=True)
        try:
            result=sampler.sample()
            self.assertEqual(result['thread_coverage'],'all_process_threads')
            self.assertIn(thread.native_id,[t['tid'] for t in result['threads']])
            self.assertTrue(all(t['cpu_ns']>=0 for t in result['threads']))
        finally:
            sampler.close();stop.set();thread.join()

    def test_self_cpu_and_memory_are_read_without_suspending_threads(self):
        from windows_resources import WindowsResources
        sampler=WindowsResources(os.getpid())
        try:
            clock_before=time.perf_counter_ns()
            before=sampler.sample()
            sum(i*i for i in range(100000))
            after=sampler.sample()
            self.assertEqual(after['status'],'available')
            self.assertGreater(after['working_set_bytes'],0)
            self.assertGreaterEqual(after['peak_working_set_bytes'],after['working_set_bytes'])
            self.assertGreaterEqual(after['process_cpu_ns'],before['process_cpu_ns'])
            self.assertGreater(after['monotonic_ns'],before['monotonic_ns'])
            # The runtime uses QueryPerformanceCounter. Python 3.11's
            # monotonic clock uses GetTickCount64 with a different epoch.
            self.assertGreaterEqual(before['monotonic_ns'],clock_before)
            self.assertLessEqual(after['monotonic_ns'],time.perf_counter_ns())
        finally:sampler.close()
        self.assertIsNone(sampler.process)


if __name__=='__main__':unittest.main()
