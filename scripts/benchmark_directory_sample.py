"""Isolated directory-name sampling comparison; no host-directory observations."""
from bisect import insort
import json
import random
import statistics
import sys
import time


def sample(values,limit,optimized):
    names=[]
    for value in values:
        if optimized:
            if limit and (len(names)<limit or value<names[-1]):
                insort(names,value)
                if len(names)>limit:names.pop()
        elif limit:
            names.append(value)
            names.sort()
            del names[limit:]
    return names


def main():
    rng=random.Random(20261104)
    values=[str(i) for i in range(10000)]
    rng.shuffle(values)
    records=[]
    for limit in (10,1000):
        samples={'baseline':[],'insertion':[]}
        for repeat in range(8):
            for name in (list(samples) if repeat%2 else list(reversed(samples))):
                start=time.perf_counter_ns()
                got=sample(values,limit,name=='insertion')
                elapsed=time.perf_counter_ns()-start
                if not (got==sorted(values)[:limit]):
                    raise SystemExit('benchmark_directory_sample: result mismatch')
                if repeat:samples[name].append(elapsed)
        records.append({'limit':limit,'samples_ns':samples,'median_ns':{k:statistics.median(v) for k,v in samples.items()}})
    print(json.dumps({'python':sys.version,'seed':20261104,'names':10000,'records':records},indent=2))


if __name__=='__main__':main()
