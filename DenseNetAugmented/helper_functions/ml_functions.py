import json
from keras import backend as K
from keras.applications import imagenet_utils
from keras.layers import Dense,Input,merge,Flatten,Dropout,LSTM,concatenate
from keras.models import Sequential,Model
from keras.preprocessing import image
from keras.utils.np_utils import to_categorical

import numpy as np

from .DenseNet import densenet
from .EfficientNet import efficientnet
from helper_functions.data_functions import get_batch_inds

from concurrent.futures import ProcessPoolExecutor
from functools import partial
import cv2

def get_cnn_model(params):
    """
    Load base CNN model and add metadata fusion layers if 'use_metadata' is set in params.py
    :param params: global parameters, used to find location of the dataset and json file
    :return model: CNN model with or without depending on params
    """

    input_tensor = Input(shape=(params['target_img_size'][0],params['target_img_size'][1],params['num_channels']))
    baseModel = densenet.DenseNetImageNet161(input_shape=(params['target_img_size'][0], params['target_img_size'][1], params['num_channels']), include_top=False, input_tensor=input_tensor)

    modelStruct = baseModel.layers[-1].output

    if params['use_metadata']:
        auxiliary_input_country = Input(shape=(params['country_count'],), name='aux_input_coun')
        auxiliary_input_continent = Input(shape=(params['continent_count'],), name='aux_input_cont')
        #print(modelStruct.shape)   # (, 2208)
        modelStruct = merge([modelStruct,auxiliary_input_country,auxiliary_input_continent],'concat')

    modelStruct = Dense(params['cnn_lstm_layer_length'], activation='relu', name='fc1')(modelStruct)
    modelStruct = Dropout(0.5)(modelStruct)
    modelStruct = Dense(params['cnn_lstm_layer_length'], activation='relu', name='fc2')(modelStruct)
    modelStruct = Dropout(0.5)(modelStruct)
    predictions = Dense(params['num_labels'], activation='softmax')(modelStruct)

    if not params['use_metadata']:
        model = Model(input=[baseModel.input], output=predictions)
    else:
        model = Model(input=[baseModel.input, auxiliary_input_country, auxiliary_input_continent], output=predictions)

    for i,layer in enumerate(model.layers):
        layer.trainable = True

    return model

def get_effnet_model(params):
    """
    Load base CNN model and add metadata fusion layers if 'use_metadata' is set in params.py
    :param params: global parameters, used to find location of the dataset and json file
    :return model: CNN model with or without depending on params
    """

    input_tensor = Input(shape=(params['target_img_size'][0],params['target_img_size'][1],params['num_channels']))
    baseModel = efficientnet.EfficientNetB7(input_shape=(params['target_img_size'][0], params['target_img_size'][1], params['num_channels']), include_top=False, input_tensor=input_tensor, pooling='avg')

    modelStruct = baseModel.layers[-1].output

    if params['use_metadata']:
        auxiliary_input_country = Input(shape=(params['country_count'],), name='aux_input_coun')
        auxiliary_input_continent = Input(shape=(params['continent_count'],), name='aux_input_cont')
        modelStruct = concatenate([modelStruct,auxiliary_input_country,auxiliary_input_continent])

    modelStruct = Dense(params['cnn_lstm_layer_length'], activation='relu', name='fc1')(modelStruct)
    modelStruct = Dropout(0.5)(modelStruct)
    modelStruct = Dense(params['cnn_lstm_layer_length'], activation='relu', name='fc2')(modelStruct)
    modelStruct = Dropout(0.5)(modelStruct)
    predictions = Dense(params['num_labels'], activation='softmax')(modelStruct)

    if not params['use_metadata']:
        model = Model(input=[baseModel.input], output=predictions)
    else:
        model = Model(input=[baseModel.input, auxiliary_input_country, auxiliary_input_continent], output=predictions)

    for i,layer in enumerate(model.layers):
        layer.trainable = True

    return model

def get_lstm_model(params, codesStats):
    """
    Load LSTM model and add metadata concatenation to input if 'use_metadata' is set in params.py
    :param params: global parameters, used to find location of the dataset and json file
    :param codesStats: dictionary containing CNN codes statistics, which are used to normalize the inputs
    :return model: LSTM model
    """

    if params['use_metadata']:
        layerLength = params['cnn_lstm_layer_length'] + params['metadata_length']
    else:
        layerLength = params['cnn_lstm_layer_length']

    model = Sequential()
    model.add(LSTM(4096, return_sequences=True, input_shape=(codesStats['max_temporal'], layerLength), dropout=0.5))
    model.add(Flatten())
    model.add(Dense(512, activation='relu'))
    model.add(Dropout(0.5))
    model.add(Dense(params['num_labels'], activation='softmax'))
    return model


'''
def _load_batch_helper(inputDict):
    """
    Helper for load_cnn_batch that actually loads imagery and supports parallel processing
    :param inputDict: dict containing the data and metadataStats that will be used to load imagery
    :return currOutput: dict with image data, metadata, and the associated label
    """

    data = inputDict['data']
    metadataStats = inputDict['metadataStats']
    metadata = np.divide(json.load(open(data['features_path'])) - np.array(metadataStats['metadata_mean']), metadataStats['metadata_max'])
    img = image.load_img(data['img_path'])
    img = image.img_to_array(img)
    labels = data['category']
    currOutput = {}
    currOutput['img'] = img
    currOutput['metadata'] = metadata
    currOutput['labels'] = labels
    # A dictionary of img as 3D numpy array, metadata after mean-normalization, label name
    return currOutput
'''

def _load_batch_helper(data_dict):
    """
    Helper for load_cnn_batch that actually loads imagery and supports parallel processing
    :param inputDict: dict containing the data and metadataStats that will be used to load imagery
    :return currOutput: dict with image data, metadata, and the associated label
    """
    loaded_data = dict()

    #img = cv2.imread(data_dict['img_path']).astype(np.float32)
    img = cv2.imread(data_dict['img_path'])
    #img = cv2.resize(img, target_img_size).astype(np.uint8)
    loaded_data['img'] = img

    loaded_data['meta_country'] = data_dict['meta_country']
    loaded_data['meta_continent'] = data_dict['meta_continent']
    loaded_data['label'] = data_dict['label']

    # A dictionary of img as 3D numpy array, metadata, label id
    return loaded_data

'''
def load_cnn_batch(params, batchData, metadataStats, executor):
    """
    Load batch of images and metadata and preprocess the data before returning.
    :param params: global parameters, used to find location of the dataset and json file
    :param batchData: list of objects in the current batch containing the category labels and paths to CNN codes and images
    :param metadataStats: metadata stats used to normalize metadata features
    :return imgdata,metadata,labels: numpy arrays containing the image data, metadata, and labels (categorical form)
    """

    futures = []
    imgdata = np.zeros((params.batch_size_cnn, params.target_img_size[0],
                        params.target_img_size[1], params.num_channels))
    metadata = np.zeros((params.batch_size_cnn, params.metadata_length))
    labels = np.zeros(params.batch_size_cnn)
    for i in range(0, len(batchData)):
        currInput = {}
        currInput['data'] = batchData[i]
        currInput['metadataStats'] = metadataStats
        task = partial(_load_batch_helper, currInput)
        futures.append(executor.submit(task))

    results = [future.result() for future in futures]
    # list of dictionaries (described in next function), one per x_data in the batch

    for i, result in enumerate(results):
        metadata[i, :] = result['metadata']
        imgdata[i, :, :, :] = result['img']
        labels[i] = result['labels']

    imgdata = imagenet_utils.preprocess_input(imgdata)
    imgdata = imgdata / 255.0

    labels = to_categorical(labels, params.num_labels)

    return imgdata, metadata, labels
'''

def load_cnn_batch(params, batchData):
    """
    Load batch of images and metadata and preprocess the data before returning.
    :param params: global parameters, used to find location of the dataset and json file
    :param batchData: list of objects in the current batch containing the category labels and paths to CNN codes and images
    :param metadataStats: metadata stats used to normalize metadata features
    :return imgdata,metadata,labels: numpy arrays containing the image data, metadata, and labels (categorical form)
    """

    futures = []
    imgdata = np.zeros((params['batch_size_cnn'], params['target_img_size'][0],
                        params['target_img_size'][1], params['num_channels']))
    metadata_country = np.zeros((params['batch_size_cnn'], params['country_count']))
    metadata_continent = np.zeros((params['batch_size_cnn'], params['continent_count']))
    labels = np.zeros(params['batch_size_cnn'])   # Extended to OH later

    # Threaded processing
    executor = ProcessPoolExecutor(max_workers=params['num_workers'])

    for i in range(len(batchData)):
        task = partial(_load_batch_helper, batchData[i])   # batchData[i] is a dictionary
        futures.append(executor.submit(task))

    # list of dictionaries (described in next function), one per x_data in the batch
    results = [future.result() for future in futures]
    executor.shutdown()

    for i, result in enumerate(results):
        metadata_country[i, :] = result['meta_country']
        metadata_continent[i, :] = result['meta_continent']
        imgdata[i, :, :, :] = result['img']
        labels[i] = result['label']

    # Preprocess and Normalize
    imgdata = imagenet_utils.preprocess_input(imgdata)
    imgdata = imgdata / 255.0

    # OH Vectorize
    labels = to_categorical(labels, params['num_labels'])

    return imgdata, metadata_country, metadata_continent, labels

'''
def img_metadata_generator(params, data, metadataStats):
    """
    Custom generator that yields images or (image,metadata) batches and their
    category labels (categorical format).
    :param params: global parameters, used to find location of the dataset and json file
    :param data: list of objects containing the category labels and paths to images and metadata features
    :param metadataStats: metadata stats used to normalize metadata features
    :yield (imgdata,labels) or (imgdata,metadata,labels): image data, metadata (if params set to use), and labels (categorical form)
    """

    N = len(data)

    idx = np.random.permutation(N)

    batchInds = get_batch_inds(params.batch_size_cnn, idx, N)

    executor = ProcessPoolExecutor(max_workers=params.num_workers)

    while True:
        for inds in batchInds:
            batchData = [data[ind] for ind in inds]
            imgdata, metadata, labels = load_cnn_batch(params, batchData, metadataStats, executor)
            if params.use_metadata:
                yield ([imgdata, metadata], labels)
            else:
                yield (imgdata, labels)
'''

def img_metadata_generator(params, data_params):
    """
    Custom generator that yields images or (image,metadata) batches and their
    category labels (categorical format).
    :param params: global parameters, used to find location of the dataset
    :yield (imgdata,labels) or (imgdata,metadata,labels): image data, metadata (if params set to use), and labels (categorical form)
    """


    N = len(data_params)

    idx = np.random.permutation(N)

    batchInds = get_batch_inds(params['batch_size_cnn'], idx, N)

    while True:
        for inds in batchInds:
            batchData = [data_params[ind] for ind in inds]
            imgdata, metadata_country, metadata_continent, labels = load_cnn_batch(params, batchData)
            if params['use_metadata']:
                yield ([imgdata, metadata_country, metadata_continent], labels)
            else:
                yield (imgdata, labels)

def codes_metadata_generator(params, data, metadataStats, codesStats):
    """
    Custom generator that yields a vector containing the CNN codes output by DenseNet and metadata features (if params set to use).
    :param params: global parameters, used to find location of the dataset and json file
    :param data: list of objects containing the category labels and paths to CNN codes and images
    :param metadataStats: metadata stats used to normalize metadata features
    :yield (codesMetadata,labels): CNN codes + metadata features (if set), and labels (categorical form)
    """

    N = len(data)

    idx = np.random.permutation(N)

    batchInds = get_batch_inds(params['batch_size_lstm'], idx, N)
    trainKeys = list(data.keys())

    executor = ProcessPoolExecutor(max_workers=params['num_workers'])

    while True:
        for inds in batchInds:
            batchKeys = [trainKeys[ind] for ind in inds]
            codesMetadata,labels = load_lstm_batch(params, data, batchKeys, metadataStats, codesStats, executor)
            yield(codesMetadata,labels)

def load_lstm_batch(params, data, batchKeys, metadataStats, codesStats, executor):
    """
    Load batch of CNN codes + metadata and preprocess the data before returning.
    :param params: global parameters, used to find location of the dataset and json file
    :param data: dictionary where the values are the paths to the files containing the CNN codes and metadata for a particular sequence
    :param batchKeys: list of keys for the current batch, where each key represents a temporal sequence of CNN codes and metadata
    :param metadataStats: metadata stats used to normalize metadata features
    :param codesStats: CNN codes stats used to normalize CNN codes and define the maximum number of temporal views
    :return codesMetadata,labels: CNN codes + metadata (if set) and labels (categorical form)
    """

    if params['use_metadata']:
        codesMetadata = np.zeros((params['batch_size_lstm'], codesStats['max_temporal'], params['cnn_lstm_layer_length']+params['metadata_length']))
    else:
        codesMetadata = np.zeros((params['batch_size_lstm'], codesStats['max_temporal'], params['cnn_lstm_layer_length']))

    labels = np.zeros(params['batch_size_lstm'])

    futures = []
    for i,key in enumerate(batchKeys):
        currInput = {}
        currInput['currData'] = data[key]
        currInput['lastLayerLength'] = codesMetadata.shape[2]
        currInput['codesStats'] = codesStats
        currInput['use_metadata'] = params['use_metadata']
        currInput['metadataStats'] = metadataStats
        labels[i] = data[key]['category']

        task = partial(_load_lstm_batch_helper, currInput)
        futures.append(executor.submit(task))

    results = [future.result() for future in futures]

    for i,result in enumerate(results):
        codesMetadata[i,:,:] = result['codesMetadata']

    labels = to_categorical(labels, params['num_labels'])
    # generates one-hot vector for each batch element -> 2D binary matrix

    return codesMetadata,labels

def _load_lstm_batch_helper(inputDict):

    currData = inputDict['currData']
    codesStats = inputDict['codesStats']
    currOutput = {}

    codesMetadata = np.zeros((codesStats['max_temporal'], inputDict['lastLayerLength']))

    timestamps = []
    for codesIndex in range(len(currData['cnn_codes_paths'])):
        cnnCodes = json.load(open(currData['cnn_codes_paths'][codesIndex]))
        # compute a timestamp for temporally sorting
        timestamp = (cnnCodes[4]-1970)*525600 + cnnCodes[5]*12*43800 + cnnCodes[6]*31*1440 + cnnCodes[7]*60
        timestamps.append(timestamp)

        cnnCodes = np.divide(cnnCodes - np.array(codesStats['codes_mean']), np.array(codesStats['codes_max']))
        codesMetadata[codesIndex,:] = cnnCodes

    sortedInds = sorted(range(len(timestamps)), key=lambda k:timestamps[k])
    codesMetadata[range(len(sortedInds)),:] = codesMetadata[sortedInds,:]

    currOutput['codesMetadata'] = codesMetadata
    return currOutput
