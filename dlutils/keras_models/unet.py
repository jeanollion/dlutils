#import numpy as np
from keras.models import Model
from keras.layers import Dense, Conv2D, Input, MaxPool2D, UpSampling2D, Concatenate, Conv2DTranspose, Dropout, Lambda
#import tensorflow as tf
from keras.optimizers import Adam
from keras import backend as K
from keras.losses import mean_squared_error
import os
from keras.preprocessing.image import array_to_img, img_to_array, load_img

def unet_down(input_layer, filters, kernel_size=3, pool=True, name=None):
    conv1L=Conv2D(filters, (kernel_size, kernel_size), padding='same', activation='relu', name=name+"_conv" if name else None)
    conv1 = conv1L(input_layer)
    residualL = Conv2D(filters, (kernel_size, kernel_size), padding='same', activation='relu', name=name+"_res" if name else None)
    residual = residualL(conv1)
    #pool_input = Dropout(dropout, name=name+"_drop" if name else None)(residual) if dropout>0 else residual
    if pool:
        max_poolL = MaxPool2D(pool_size=(2,2), name=name)
        max_pool = max_poolL(pool_input)
        return max_pool, residual, (conv1L, residualL, max_poolL)
    else:
        return residual, (conv1L, residualL)

def unet_down_concat(input_layer, layers_to_concatenate, filters, kernel_size=3, pool=True, name=None):
    conv1L = Conv2D(filters, (kernel_size, kernel_size), padding='same', activation='relu', name=name+"_conv" if name else None)
    conv1 = conv1L(input_layer)
    concatL=Concatenate(axis=3, name=name+"_concat" if name else None)
    concat = concatL([conv1]+layers_to_concatenate)
    residualL = Conv2D(filters, (kernel_size, kernel_size), padding='same', activation='relu', name=name+"_res" if name else None)
    residual = residualL(concat)
    #pool_input = Dropout(dropout, name=name+"_drop" if name else None)(residual) if dropout>0 else residual
    if pool:
        max_poolL = MaxPool2D(pool_size=(2,2), name=name)
        max_pool =max_poolL(residual)
        return max_pool, residual, (conv1L, concatL, residualL)
    else:
        return residual, (conv1L, concatL, residualL)

def get_slice_channel_layer(channel, name=None):
    return Lambda(lambda x: x[:,:,:,channel:(channel+1)], name = (name if name else "get_channel")+"_"+str(channel))

class UnetEncoder():
    def __init__(self, n_down, n_filters, image_shape=None, name="encoder"):
        if image_shape!=None and min(image_shape[0], image_shape[1])/2**n_down<1:
            raise ValueError("too many down convolutions. minimal dimension is {}, number of convlution is {} shape in minimal dimension would be {}".format(min(image_shape[0], image_shape[1]), n_down, min(image_shape[0], image_shape[1])/2**n_down))
        self.image_shape = image_shape
        self.name=name
        self.layers=[]
        self.n_down = n_down
        self.n_filters=n_filters
        for layer_idx in range(n_down + 1): # +1 -> last feature layer
            self._make_layer(layer_idx)

    def encode(self, input, layers_to_concatenate=None):
        if layers_to_concatenate and len(layers_to_concatenate)!=self.n_down+1:
            raise ValueError("{} layers to concatenate are provieded whereas {} are needed".format(len(layers_to_concatenate), self.n_down+1))
        residuals = []
        last_input = input
        for layer_idx in range(self.n_down):
            last_input, res = self._encode_layer(last_input, layer_idx, layers_to_concatenate[layer_idx] if layers_to_concatenate else None)
            residuals.append(res)
        feature_layer = self._encode_layer(last_input, self.n_down,  layers_to_concatenate[self.n_down] if layers_to_concatenate else None)
        return feature_layer, residuals

    def _make_layer(self, layer_idx):
        filters = self._get_n_filters(layer_idx)
        kernel_size = self._get_kernel_size(layer_idx)
        conv1L = Conv2D(filters, (kernel_size, kernel_size), padding='same', activation='relu', kernel_initializer = 'he_normal', name=self.name+str(layer_idx+1)+"_conv" if self.name else None)
        concatL=Concatenate(axis=3, name=self.name+str(layer_idx+1)+"_concat" if self.name else None)
        residualL = Conv2D(filters, (kernel_size, kernel_size), padding='same', activation='relu', kernel_initializer = 'he_normal', name=self.name+str(layer_idx+1)+"_res" if self.name else None)
        if len(self.layers)==self.n_down:
            self.layers.append([conv1L, concatL, residualL])
        else:
            max_poolL = MaxPool2D(pool_size=(2,2), name=self.name+str(layer_idx+1) if self.name else None)
            self.layers.append([conv1L, concatL, residualL, max_poolL])

    def _encode_layer(self, input, layer_idx, layers_to_concatenate=None):
        layers = self.layers[layer_idx]
        conv1 = layers[0](input)
        if layers_to_concatenate:
            conv1 = layers[1]([conv1]+layers_to_concatenate)
        residual = layers[2](conv1)
        if layer_idx==self.n_down:
            return residual
        else:
            max_pool = layers[3](residual)
            return max_pool, residual

    def _get_kernel_size(self, layer_idx):
        if not self.image_shape:
            return 3
        min_dim = min(self.image_shape[0], self.image_shape[1])
        current_size = min_dim // 2**layer_idx
        return 3 if current_size>=3 else current_size

    def _get_n_filters(self, layer_idx):
        return self.n_filters * 2**layer_idx

class UnetDecoder():
    def __init__(self, n_up, n_filters, name="decoder"):
        self.layers=[]
        self.name=name
        self.n_up = n_up
        self.n_filters = n_filters
        for layer_idx in range(n_up):
            self._make_layer(self._get_n_filters(layer_idx), layer_idx)

    def _make_layer(self, filters, layer_idx):
        filters=int(filters)
        upsampleL = UpSampling2D(size=(2,2), name = self.name+str(layer_idx+1)+"_up" if self.name else None)
        upconvL = Conv2D(filters, kernel_initializer = 'he_normal', kernel_size=(2, 2), padding="same", name = self.name+str(layer_idx+1)+"_conv1" if self.name else None)
        concatL = Concatenate(axis=3, name = self.name+str(layer_idx+1)+"_concat" if self.name else None)
        conv1L = Conv2D(filters, (3, 3), padding='same', activation='relu', kernel_initializer = 'he_normal', name = self.name+str(layer_idx+1)+"_conv2" if self.name else None)
        conv2L = Conv2D(filters, (3, 3), padding='same', activation='relu', kernel_initializer = 'he_normal', name = self.name+str(layer_idx+1)+"_conv3" if self.name else None)
        self.layers.append([upsampleL, upconvL, concatL, conv1L, conv2L])

    def decode(self, input, residuals, return_all=False):
        if len(residuals)!=self.n_up:
            raise ValueError("#{} residuals are provided whereas {} are needed".format(len(residuals), self.n_up))
        last_input = input
        all_activations = []
        for layer_idx in range(self.n_up):
            last_input = self._decode_layer(last_input, residuals[-layer_idx-1], layer_idx)
            if return_all:
                all_activations.append(last_input)
        if return_all:
            return all_activations
        else:
            return last_input

    def encode_and_decode(self, input, encoder, layers_to_concatenate=None):
        if encoder.n_down!=self.n_up:
            raise ValueError("encoder has {} enconding blocks whereas decoder has {} decoding blocks".format(enconder.n_down, self.n_up))
        encoded, residuals = encoder.encode(input, layers_to_concatenate)
        return self.decode(encoded, residuals)

    def _decode_layer(self, input, residual, layer_idx):
        layers = self.layers[layer_idx]
        upsample = layers[0](input)
        upconv = layers[1](upsample)
        concat = layers[2]([residual, upconv])
        conv1 = layers[3](concat)
        conv2 = layers[4](conv1)
        return conv2

    def _get_n_filters(self, layer_idx):
        n= self.n_filters * 2**(self.n_up - layer_idx - 1)
        return n

def get_unet_model(image_shape, n_down, filters=64, n_output_channels=1, out_activations=["linear"]):
    encoder = UnetEncoder(n_down, filters, image_shape)
    decoder = UnetDecoder(n_down, filters)
    if not isinstance(out_activations, list):
        out_activations=[out_activations]
    if len(out_activations)==1 and n_output_channels>1:
         out_activations = out_activations*n_output_channels
    input = Input(shape = image_shape+(1,), name="input")
    last_decoded = decoder.encode_and_decode(input, encoder)
    out = [Conv2D(filters=1, kernel_size=(1, 1), activation=out_activations[i])(last_decoded) for i in range(n_output_channels)]
    return Model(input, out)
    # todo make several decoders for each output ? as an option ?

def get_edm_displacement_model(filters=64, image_shape = (256, 32), edm_prop=0.5):
    encoder = UnetEncoder(4, filters, image_shape+(2,))
    decoder = UnetDecoder(4, filters)
    input = Input(shape = image_shape+(2,), name="input")
    encoded, residuals = encoder.encode(input)
    all_activations = decoder.decode(encoded, residuals, return_all=True)
    edm = Conv2D(filters=1, kernel_size=(1, 1), activation="linear")(all_activations[-1])
    dy_input = unet_up(all_activations[-2], residual=residuals[0], filters=filters)
    dy = Conv2D(filters=1, kernel_size=(1, 1), activation="linear")(dy_input)
    out = Concatenate(axis=3)([edm, dy])

    model = Model(input, out)

    def make_loss(y_true, y_pred, epsilon = 0.1, edm_prop=edm_prop):
      input_size = K.shape(y_true)[1] * K.shape(y_true)[2]
      batch_size = K.shape(y_true)[0]
      yt_edm = K.reshape(y_true[:,:,:, 0], (batch_size, input_size))
      yp_edm = K.reshape(y_pred[:,:,:, 0], (batch_size, input_size))
      yt_dis = K.reshape(y_true[:,:,:, 1], (batch_size, input_size))
      yp_dis = K.reshape(y_pred[:,:,:, 1], (batch_size, input_size))

      edm_loss = mean_squared_error(yt_edm, yp_edm)
      #dis_loss = K.mean( K.mean( (yt_edm + epsilon ) * K.square(yt_dis - yp_dis) , axis=-1) ) #/ # idée: relacher les contraintes aux bords des cellules
      #dis_loss = K.mean( K.sum( (yt_edm + epsilon ) * K.square(yt_dis - yp_dis) , axis=-1) / (K.sum(yt_edm + epsilon, axis=-1)) )
      dis_loss = mean_squared_error(yt_dis, yp_dis)
      return edm_prop * edm_loss + (1 - edm_prop) * dis_loss

    model.compile(optimizer=Adam(1e-3), loss=make_loss)
    return model

def get_edm_displacement_untangled_model(filters_dy=24, filters_edm=24, image_shape = (256, 32), edm_prop=0.5, concat_output=False, edm_trainable=False):
    # make a regular Unet for edm regression
    edm_encoder = UnetEncoder(4, filters_edm, image_shape, name="edm")
    edm_decoder = UnetDecoder(4, filters_edm, name="edm")
    edm_input = Input(shape = image_shape+(1,), name = "edm_input")
    edm_encoded, edm_residuals = edm_encoder.encode(edm_input)
    edm_last = edm_decoder.decode(edm_encoded, edm_residuals)
    edmL = Conv2D(filters=1, kernel_size=(1, 1), activation="linear", name = "edm_output")
    edm_model_simple=Model(edm_input, edmL(edm_last))
    if not edm_trainable:
        for layer in edm_model_simple.layers:
            layer.trainable=False
    edm_model_simple.compile(optimizer=Adam(1e-3), loss='mean_squared_error')

    edm_model_encoder = Model(edm_input, edm_residuals+[edm_encoded], name="edm_encoder")
    # make a unet model for displacement with concatenated feature layer

    dy_input = Input(shape = image_shape+(3,), name = "dy_input")
    input_prev = get_slice_channel_layer(0, name = "dy_input")(dy_input)
    input_cur = get_slice_channel_layer(1, name = "dy_input")(dy_input)
    input_next = get_slice_channel_layer(2, name = "dy_input")(dy_input)
    edm_prev_out = edm_model_encoder(input_prev)
    edm_cur_out = edm_model_encoder(input_cur)
    edm_cur_last = edm_decoder.decode(edm_cur_out[-1], edm_cur_out[:-1])
    edm_cur = edmL(edm_cur_last)
    edm_next_out = edm_model_encoder(input_next)
    layers_to_concatenate = [[edm_prev_out[i], edm_cur_out[i], edm_next_out[i]] for i in range(0, len(edm_prev_out))]

    dy_encoder = UnetEncoder(4, filters_dy, image_shape, name="dy")
    dy_encoded, dy_residuals = dy_encoder.encode(dy_input, layers_to_concatenate)

    if concat_output:
        dy_decoder = UnetDecoder(4, filters_dy, name="dy")
        dy_last = dy_decoder.decode(dy_encoded, dy_residuals)
        dy = Conv2D(filters=2, kernel_size=(1, 1), activation="linear", name = "dy_output")(dy_last)
        out = Concatenate(axis=3, name="edm_dy_output")([edm_cur, dy])
        dy_model =  Model(dy_input, out)
        dy_model.compile(optimizer=Adam(1e-3), loss='mean_squared_error')
    else:
        dy_prev_decoder = UnetDecoder(4, n_filters=filters_dy//2, name="dy_prev")
        dy_prev_last = dy_prev_decoder.decode(dy_encoded, dy_residuals)
        dy_prev = Conv2D(filters=1, kernel_size=(1, 1), activation="linear", name = "dy_output")(dy_prev_last)
        dy_next_decoder = UnetDecoder(4, n_filters=filters_dy//2, name="dy_next")
        dy_next_last = dy_next_decoder.decode(dy_encoded, dy_residuals)
        dy_next = Conv2D(filters=1, kernel_size=(1, 1), activation="linear", name = "dy_next_output")(dy_next_last)
        dy_model_train =  Model(dy_input, [dy_prev, dy_next])
        dy_model_train.compile(optimizer=Adam(1e-3), loss=['mean_squared_error', 'mean_squared_error'])
        dy_model_predict =  Model(dy_input, [edm_cur, dy_prev])
        dy_model_predict.compile(optimizer=Adam(1e-3), loss=['mean_squared_error', 'mean_squared_error'], loss_weights=[50, 1])

    return edm_model_simple, dy_model_train, dy_model_predict
