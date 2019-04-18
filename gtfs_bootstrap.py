import csv
import certifi
from elasticsearch import Elasticsearch
from elasticsearch.client.indices import IndicesClient
from elasticsearch.helpers import parallel_bulk 


gtfs_docs_path="<PATH>"

def remove_prefix(text: str, prefix: str):
    if text.startswith(prefix):
        return text[len(prefix):]
    return text

def remove_prefix_from_dict(prefix: str, d: dict):
    new_d = dict()
    for k in d.keys():
        if isinstance(k, str):
            new_d[remove_prefix(k, prefix)] = d[k]
        else:
            new_d[k] = d[k]
    return new_d

def gather_shapes():
    shapes = dict()
    with open(gtfs_docs_path + "shapes.txt", 'r' ) as shapes_file:
        reader = csv.DictReader(shapes_file)
        for line in reader:
            shape_id = int(line['shape_id'])
            if shape_id not in shapes:
                shapes[shape_id] = {
                    "id": shape_id, 
                    "path": {"type": "linestring", "coordinates" : [[float(line["shape_pt_lon"]), float(line["shape_pt_lat"])]] },
                    "start": line["shape_pt_lat"] + "," + line["shape_pt_lon"],
                    "finish": line["shape_pt_lat"] + "," + line["shape_pt_lon"],
                    "start_seq": int(line["shape_pt_sequence"]), "finish_seq": int(line["shape_pt_sequence"])
                    }
            else:
                shapes[shape_id]["path"]["coordinates"].append([float(line["shape_pt_lon"]), float(line["shape_pt_lat"])])
                if shapes[shape_id]["start_seq"] > int(line["shape_pt_sequence"]):
                    shapes[shape_id]["start"] = line["shape_pt_lat"] + "," + line["shape_pt_lon"]
                    shapes[shape_id]["start_seq"] = int(line["shape_pt_sequence"])
                if shapes[shape_id]["finish_seq"] < int(line["shape_pt_sequence"]):
                    shapes[shape_id]["finish"] = line["shape_pt_lat"] + "," + line["shape_pt_lon"]
                    shapes[shape_id]["finish_seq"] = int(line["shape_pt_sequence"])
    return shapes


def gather_stops():
    stops = dict()
    with open(gtfs_docs_path + "stops.txt", 'r' ) as stops_file:
        reader = csv.DictReader(stops_file)
        for line in reader:
            id = int(line['stop_id'])
            lat = line.pop('stop_lat', None)
            lon = line.pop('stop_lon', None)
            if lat and lon:
                line['pos'] = lat + ',' + lon
            stops[id] = remove_prefix_from_dict("stop_", line)
    return stops
    
            
def gather_trips():
    trips = dict()
    with open(gtfs_docs_path + "trips.txt", 'r' ) as trips_file:
        reader = csv.DictReader(trips_file)
        for line in reader:
            trip_id = int(line['trip_id'])
            trips[trip_id] = remove_prefix_from_dict("trip_", line)
    return trips

def gather_routes():
    routes = dict()
    with open(gtfs_docs_path + "routes.txt", 'r' ) as routes_file:
        reader = csv.DictReader(routes_file)
        for line in reader:
            route_id = int(line['route_id'])
            routes[route_id] = remove_prefix_from_dict("route_", line) 
    return routes

def gather_stop_times():
    stop_times = []
    with open(gtfs_docs_path + "stop_times.txt", 'r' ) as stop_times_file:
        reader = csv.DictReader(stop_times_file)
        for line in reader:
            stop_times.append(line)
    return stop_times

def gather_transfers():
    transfers = []
    with open(gtfs_docs_path + "transfers.txt", 'r' ) as transfers_file:
        reader = csv.DictReader(transfers_file)
        for line in reader:
            transfers.append(line)

    return transfers

def genbulkactions(index: str, docs: list):
    for doc in docs:
        yield {
            "_index": index,
            "_source": doc,
        }

def shape_to_route_dict(trips: list, routes: dict):
    shapes_to_route=dict()
    for trip in trips:
        shape_id = int(trip['shape_id'])
        route_id = int(trip['route_id'])
        if shape_id not in shapes_to_route:
            shapes_to_route[shape_id] = routes[route_id]

    return shapes_to_route


def main():
    index_prefix = "via"
    stops_index = index_prefix + "_stops"
    shapes_index = index_prefix + "_shapes"
    stop_times_index = index_prefix + "_stop_times"
   # es = Elasticsearch(
   #     host="<YOURHOST>",
   #     scheme="https",
   #     port=9243,
   #     http_auth=("<USERNAME>", "<PASSWORD"),
   #     use_ssl=True,
   #     verify_certs=True,
   #     ca_certs=certifi.where())
    es = Elasticsearch()
    with open("mappings/shapes.json", 'r' ) as shapes_mapping_file:
        shapes_mapping = shapes_mapping_file.read()
    
    with open("mappings/stops.json", 'r' ) as stops_mapping_file:
        stops_mapping = stops_mapping_file.read()

    with open("mappings/stop_times.json", 'r') as stop_times_file:
        stop_times_mapping = stop_times_file.read()
    
    indices = IndicesClient(es)
    indices.create(stops_index, body=stops_mapping)
    indices.create(shapes_index, body=shapes_mapping)
    indices.create(stop_times_index, body=stop_times_mapping)
    all_stops = gather_stops()
    for ok, item in parallel_bulk(es, genbulkactions(stops_index, all_stops.values()), chunk_size=500):
        if not ok:
            print(item)
    
    print("Done with stops")

    all_shapes = gather_shapes()
    all_trips = gather_trips()
    all_routes = gather_routes()
    shapes_to_route = shape_to_route_dict(all_trips.values(), all_routes)
    for shape_id in shapes_to_route.keys():
        all_shapes[shape_id]['route'] = shapes_to_route[shape_id]
        all_shapes[shape_id].pop('start_seq', None)
        all_shapes[shape_id].pop('finish_seq', None)

    for ok, item in parallel_bulk(es, genbulkactions(shapes_index, all_shapes.values()), chunk_size=500):
        if not ok:
            print(item)
    
    print("Done with shapes")
    for trip in all_trips.values():
        route_id = trip.pop("route_id", None)
        if route_id:
            trip['route'] = all_routes[int(route_id)]

    all_stop_times = gather_stop_times()
    for stop_time in all_stop_times:
        trip_id = stop_time.pop("trip_id", None)
        stop_id = stop_time.pop("stop_id", None)
        if trip_id:
            stop_time['trip'] = all_trips[int(trip_id)]
        if stop_id:
            stop_time['stop'] = all_stops[int(stop_id)]

    for ok, item in parallel_bulk(es, genbulkactions(stop_times_index, all_stop_times), chunk_size=1000):
        if not ok:
            print(item) 

    print("Done with stop times")

if __name__ == "__main__":
    main()
