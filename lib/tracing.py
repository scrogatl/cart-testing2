#tracing call for Jaeger
#initializing the jaeger tracer
import logging
import sys
import os
from jaeger_client import Config

from os import environ

if environ.get('TRACER_HOST') is not None:
    if os.environ['TRACER_HOST'] != "":
        jaegerhost=os.environ['TRACER_HOST']
    else:
        jaegerhost='localhost'
else:
    jaegerhost='localhost'


if environ.get('TRACER_PORT') is not None:
    if os.environ['TRACER_PORT'] != "":
        jaegerport=os.environ['TRACER_PORT']
    else:
        jaegerport=6832
else:
    jaegerport=6832


def init_tracer(service, app):
    logging.getLogger('').handlers=[]
    logging.basicConfig(format='%(message)s', level=logging.DEBUG)

    app.logger.info("init_tracer - service: %s", service)
    app.logger.info("init_tracer - jaegerhost: %s", jaegerhost)
    app.logger.info("init_tracer - jaegerport: %s", jaegerport)

    config=Config(
        config={
            'sampler':{
                'type':'const',
                'param':1
            },
           'local_agent': {
               'reporting_host': jaegerhost,
               'reporting_port': jaegerport,
           },
            'logging':True
        },
        service_name=service,
        validate=True
    )
    tracer = config.initialize_tracer()
    with tracer.start_span('TestSpan') as span:
        span.log_kv({'event': 'test message', 'life': 42})
        span.log_kv({'event': 'test message2', 'life': 42})
        span.log_kv({'event': 'test message3', 'life': 42})
    if tracer is None:
        Config._initialized = False
        tracer = config.initialize_tracer()
    return tracer
    # tracer.close()
    # return config.initialize_tracer()
