#!/usr/bin/python

# general imports
import json
import requests
import time

from logging.config import dictConfig
from os import environ
from flask import render_template, jsonify
from flask import request
from flask_httpauth import HTTPTokenAuth
from flask import Flask
from redis_conn import redis_connection

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor                                        
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import \
    TraceContextTextMapPropagator
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.instrumentation.redis import RedisInstrumentor

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    }},
    'handlers': {'wsgi': {
        'class': 'logging.StreamHandler',
        'stream': 'ext://flask.logging.wsgi_errors_stream',
        'formatter': 'default'
    }},
    'root': {
        'level': 'DEBUG',
        'handlers': ['wsgi'],
        'propagate': True,
    }
})

# set variables with env variables

cart_port = environ['CART_PORT'] if environ.get('CART_PORT') not in (None, '') else 5000

auth_url = environ['AUTH_URL'] if environ.get('AUTH_URL') not in (None, '') else ''

auth_mode = int(environ['AUTH_MODE']) if environ.get('AUTH_MODE') not in (None, '') else 1


# initializing flask
app = Flask(__name__)
# FlaskInstrumentor().instrument_app(app)



# Service name is required for most backends
resource = Resource(attributes={
      "service.name": "acme-cart",
      "application": "acme-fitness"
    })

provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://wavefront-proxy.scrog.svc.cluster.local:4317"))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("acme-fitness.tracer")

def set_cloud_role(envelope):
    envelope.tags['ai.cloud.role'] = 'cart-service'

app.debug = True
auth = HTTPTokenAuth('Bearer')

# initializing redis connections on localhost and port 6379
# If error terminates process- entire cart is shut down
RedisInstrumentor().instrument()
rConn = redis_connection(app.logger)

# errorhandler for specific responses
class FoundIssue(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        return rv


@auth.verify_token
def verify_token(token):
    global auth_mode

    headers = {'content-type': 'application/json'}
    verify_token_url = auth_url + "/verify-token"
    login_url = auth_url + "/login"

    app.logger.info("user service mode in verify_token is %s", auth_mode)
    if auth_mode == 2:
        print("using local version of user for test - getting token")

        data1 = json.dumps({"username": "eric", "password": "vmware1!"})

        r = requests.post(login_url, headers=headers, data=data1)

        if r.status_code == 200:
            verify_token_payload = json.dumps({"access_token": json.loads(r.content)["access_token"]})
            r = requests.post(verify_token_url, headers=headers, data=verify_token_payload)
            if r.status_code == 200:
                app.logger.info('Authorized %s', json.loads(r.content)["message"])
                return True
            else:
                app.logger.info('Un-authorized %s', json.loads(r.content)["message"])
                return False
        else:
            app.logger.info('Bad user or password %s', json.loads(r.content)["message"])
            return False

    elif auth_mode == 1:
        if token == "":
            app.logger.info("No Bearer token sent")
            return False
        else:
            verify_token_payload = json.dumps({"access_token": token})
            r = requests.post(verify_token_url, headers=headers, data=verify_token_payload)
            if r.status_code == 200:
                app.logger.info('Authorized %s', str(r.content))
                return True
            else:
                app.logger.info('Un-authorized %s', str(r.content))
                return False

    else:
        return True


@app.errorhandler(FoundIssue)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


# initialization of redis with fake data from the San Francisco legal offices of Shri, Dan and Bill SDB.
def insert_data():
    app.logger.info('inserting data')

    rConn.flushall()

    keys = ['bill', 'dan', 'shri']

    data = [
        {'itemid': 'sdfsdfsfs', 'name': 'fitband', 'description': 'fitband for any age - even babies', 'quantity': 1,
         'price': 4.5},
        {'itemid': 'sfsdsda3343', 'name': 'redpant', 'description': 'the most awesome redpants in the world',
         'quantity': 1, 'price': 400},
    ]

    payload = json.dumps(data)

    for x in keys:
        rConn.set(x, payload)


# Gets all items from a specific userid
def get_items(userid):
    app.logger.info('/cart/getItems')
    unpacked_data = ''
    with tracer.start_as_current_span("GET cart_items") as span:
        if rConn.exists(userid):
            unpacked_data = json.loads(rConn.get(userid).decode('utf-8'))
            app.logger.info('got data')
            span.add_event("got data", {"event_attributes": 1})
        else:
            app.logger.info('empty - no data for key %s', userid)
            unpacked_data = 0
    return unpacked_data


# convert string to number
def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


# http call to gets all Items from a cart (userid)
# If successful this returns the cart and items, if not successfull (the user id is non-existant) - 204 returned

# @statsd.timer('get_cart_items')
@app.route('/cart/items/<userid>', methods=['GET'])
@auth.login_required
def get_cart_items(userid):
    # app.logger.info('the request headers are %s', str(request.headers))
    return_value = '200'

    with tracer.start_as_current_span("GET cart_items_for_user") as span:
        app.logger.info('getting all items on cart for user %s', userid)

    pp_table = get_items(userid)
    if pp_table:
        packed_data = jsonify({"userid": userid, "cart": pp_table})
    else:
        app.logger.info('no items in cart found for %s', userid)
        packed_data = jsonify({"userid": userid, "cart": pp_table})
        return_value = '204'

    return packed_data, return_value


# gets total items in users cart
@app.route('/cart/items/total/<userid>', methods=['GET', 'POST'])
@auth.login_required
def cart_items_total(userid):
    function_name = '/cart/items/total'
    with tracer.start_as_current_span("GET items_total") as span:
        app.logger.info('getting total for %s cart', userid)

    jsonobj = get_items(userid)

    key_list = []
    for item in jsonobj:
        key_list.append(list(item.keys())[0])

    key_index = 0
    total = 0

    while key_index < len(jsonobj):
        quantity = jsonobj[key_index]['quantity']
        if is_number(quantity):
            total = total + float(quantity)
        else:
            total = total + 0
        key_index += 1

    app.logger.info("The total number of items is %s", str(total))
    total_json = {"userid": userid, "cartitemtotal": total}

    return jsonify(total_json)


# http call to get all carts and their values
# @statsd.timer('get_all_carts')
@app.route('/cart/all', methods=['GET'])
@auth.login_required
def get_all_carts():
    app.logger.info('getting carts')

    carts = []
    cart = {}

    for x in rConn.keys():
        clean_key = x.decode('utf-8')
        cart['id'] = clean_key
        cart['cart'] = json.loads(rConn.get(clean_key).decode('utf-8'))
        carts.append(cart)
        cart = {}

    return jsonify({'all carts': carts})


# http call to add an item - if user id non-existent - this will add the user into the database or it will
# concatenate the item to the existing carts example curl call to test: curl --header "Content-Type:
# application/json" --request POST --data '{"mytext":"xyz", "idname":"1234"}' http://34.215.155.50:5000/additem/bill
# If add is positive returns the userid @statsd.timer('add_item')
@app.route('/cart/item/add/<userid>', methods=['GET', 'POST'])
@auth.login_required
def add_item(userid):
    content = request.json

    with tracer.start_as_current_span("POST add_item") as span:
        app.logger.info('the content to add is %s', content)
        span.add_event("adding data... will add actuall data item later")


        jsonobj = get_items(userid)

        if jsonobj:

            key_index = 0
            while key_index < len(jsonobj):
                if jsonobj[key_index]['itemid'] == content['itemid']:
                    jsonobj[key_index]['quantity'] = int(jsonobj[key_index]['quantity']) + int(content['quantity'])
                    key_index = len(jsonobj) + 1
                    payload = json.dumps(jsonobj)
                    try:
                        app.logger.info('inserting cart for %s with following contents %s', userid, json.dumps(content))
                        rConn.set(userid, payload)
                    except Exception as e:
                        app.logger.error('Could not insert data %s into redis, error is %s', json.dumps(content), e)
                else:
                    key_index += 1

            if key_index <= len(jsonobj):
                jsonobj.append(content)
                payload = json.dumps(jsonobj)
                try:
                    app.logger.info('inserting cart for %s with following contents %s', userid, json.dumps(content))
                    rConn.set(userid, payload)
                except Exception as e:
                    app.logger.error('Could not insert data %s into redis, error is %s', json.dumps(content), e)

        else:
            payload = [content]
            app.logger.info("added to payload for new insert %s", json.dumps(payload))
            try:
                rConn.set(userid, json.dumps(payload))
            except Exception as e:
                app.logger.error('Could not insert data %s into redis, error is %s', json.dumps(content), e)

    return jsonify({"userid": userid})


@app.route('/cart/modify/<userid>', methods=['GET', 'POST'])
@auth.login_required
def replace_cart(userid):
    content = request.json

    with tracer.start_as_current_span("POST replace_cart") as span:

        app.logger.info('the content to modify is %s', content)

        jsonobj = get_items(userid)

        payload = []
        for item in content['cart']:
            payload.append(item)

        app.logger.info("added to payload for new insert %s", json.dumps(payload))
        try:
            rConn.set(userid, json.dumps(payload))
        except Exception as e:
            app.logger.error('Could not insert data %s into redis, error is %s', json.dumps(content), e)

    return jsonify({"userid": userid})


# clear item from cart
# minimum content must be {"itemid":"shjhjssr", "quantity":"x"}
@app.route('/cart/item/modify/<userid>', methods=['GET', 'POST'])
@auth.login_required
def delete_item(userid):
    
    content = request.json

    with tracer.start_as_current_span("POST delete_item") as span:
        app.logger.info('the item to delete is %s', content)

    jsonobj = get_items(userid)
    if jsonobj:
        key_index = 0
        while key_index < len(jsonobj):
            if (jsonobj[key_index]['itemid'] == content['itemid']) and (content['quantity'] == 0):
                del jsonobj[key_index]
                payload = json.dumps(jsonobj)
                try:
                    app.logger.info('removing item for %s with following contents %s', userid, json.dumps(content))
                    rConn.set(userid, payload)
                except Exception as e:
                    app.logger.error('Could not remove data %s into redis, error is %s', json.dumps(content), e)
                key_index = len(jsonobj)
            elif jsonobj[key_index]['itemid'] == content['itemid']:
                jsonobj[key_index]['quantity'] = content['quantity']
                payload = json.dumps(jsonobj)
                try:
                    app.logger.info('modifying cart for %s with following contents %s', userid, json.dumps(content))
                    rConn.set(userid, payload)
                    app.logger.info('finished setting %s with following contents %s', userid, json.dumps(content))
                except Exception as e:
                    app.logger.error('Could not modify cart %s into redis, error is %s', json.dumps(content), e)
                key_index = len(jsonobj)
            else:
                key_index += 1
    else:
        app.logger.info('no items in cart found for %s', userid)
        output_message = "no cart found for " + userid
        raise FoundIssue(str(output_message), status_code=204)

    return jsonify({"userid": userid})


# clear cart
@app.route('/cart/clear/<userid>', methods=['GET', 'POST'])
@auth.login_required
def clear_cart(userid):
    with tracer.start_as_current_span("POST clear_cart") as span:
        app.logger.info("clearing cart for %s", userid)

    try:
        rConn.delete(userid)
    except Exception as e:
        app.logger.error('Could not delete %s cart due to %s', userid, e)
        raise FoundIssue(str(e), status_code=500)

    return '', 200


# placeholder for call to order
@app.route('/order/userid')
def order(userid):
    return render_template('hello.html')


# get total amount in users cart
@app.route('/cart/total/<userid>', methods=['GET', 'POST'])
@auth.login_required
def cart_total(userid):
    app.logger.info('getting total for %s cart', userid)

    jsonobj = get_items(userid)

    key_list = []
    for item in jsonobj:
        key_list.append(list(item.keys())[0])

    key_index = 0
    total = 0

    while key_index < len(jsonobj):
        quantity = jsonobj[key_index]['quantity']
        price = jsonobj[key_index]['price']
        #        quantity=jsonobj[key_index][key_list[key_index]]['quantity']
        #        price=jsonobj[key_index][key_list[key_index]]['price']
        if is_number(quantity) and is_number(price):
            total = total + (float(quantity) * float(price))
        else:
            total = total + 0
        key_index += 1

    app.logger.info("The total calculated is %s", str(total))

    total_json = {"userid": userid, "carttotal": total}

    return jsonify(total_json)


# baseline route to check is server is live ;-)
@app.route('/')
def hello_world(name=None):
    return render_template('hello.html')


@app.route('/env', methods=['GET'])
def get_env():
    return jsonify({'redis_info': rConn.info()})


if __name__ == '__main__':
    insert_data()  # initialize the database with some baseline
    app.run(host='0.0.0.0', port=cart_port)
    time.sleep(2)
