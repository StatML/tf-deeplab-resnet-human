"""Evaluation script for the DeepLab-ResNet network on the validation subset
   of PASCAL VOC dataset.

This script evaluates the model on 1449 validation images.
"""

from __future__ import print_function

import argparse
from datetime import datetime
import os
import sys
import time
import scipy.misc
import scipy.io as sio

import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
from deeplab_resnet import DeepLabResNetModel, ImageReader, prepare_label

n_classes = 20

DATA_DIRECTORY = './dataset/human'
DATA_LIST_PATH = './dataset/human/list/val.txt'
NUM_STEPS = 10000 # Number of images in the validation set.
RESTORE_FROM = './snapshots'

def get_arguments():
    """Parse all the arguments provided from the CLI.
    
    Returns:
      A list of parsed arguments.
    """
    parser = argparse.ArgumentParser(description="DeepLabLFOV Network")
    parser.add_argument("--data-dir", type=str, default=DATA_DIRECTORY,
                        help="Path to the directory containing the PASCAL VOC dataset.")
    parser.add_argument("--data-list", type=str, default=DATA_LIST_PATH,
                        help="Path to the file listing the images in the dataset.")
    parser.add_argument("--num-steps", type=int, default=NUM_STEPS,
                        help="Number of images in the validation set.")
    parser.add_argument("--restore-from", type=str, default=RESTORE_FROM,
                        help="Where restore model parameters from.")
    return parser.parse_args()

def load(saver, sess, ckpt_path):
    '''Load trained weights.
    
    Args:
      saver: TensorFlow saver object.
      sess: TensorFlow session.
      ckpt_path: path to checkpoint file with parameters.
    ''' 
    ckpt = tf.train.get_checkpoint_state(ckpt_path)
    if ckpt and ckpt.model_checkpoint_path:
        ckpt_name = os.path.basename(ckpt.model_checkpoint_path)
        saver.restore(sess, os.path.join(ckpt_path, ckpt_name))
        print("Restored model parameters from {}".format(ckpt_name))
        return True
    else:
        return False   
    # saver.restore(sess, tf.train.latest_checkpoint(ckpt_path))
    # saver.restore(sess, ckpt_path)
    

def main():
    """Create the model and start the evaluation process."""
    args = get_arguments()
    
    # Create queue coordinator.
    coord = tf.train.Coordinator()
    
    # Load reader.
    with tf.name_scope("create_inputs"):
        reader = ImageReader(
            args.data_dir,
            args.data_list,
            None, # No defined input size.
            False, # No random scale.
            False, # No random mirror.
            coord)
        image, label = reader.image, reader.label

    image_batch, label_batch = tf.expand_dims(image, dim=0), tf.expand_dims(label, dim=0) # Add one batch dimension.
    h_orig, w_orig = tf.to_float(tf.shape(image_batch)[1]), tf.to_float(tf.shape(image_batch)[2])
    image_batch075 = tf.image.resize_images(image_batch, tf.stack([tf.to_int32(tf.multiply(h_orig, 0.75)), tf.to_int32(tf.multiply(w_orig, 0.75))]))
    image_batch05 = tf.image.resize_images(image_batch, tf.stack([tf.to_int32(tf.multiply(h_orig, 0.5)), tf.to_int32(tf.multiply(w_orig, 0.5))]))
    
    # Create network.
    with tf.variable_scope('', reuse=False):
        net = DeepLabResNetModel({'data': image_batch}, is_training=False, n_classes=n_classes)
    with tf.variable_scope('', reuse=True):
        net075 = DeepLabResNetModel({'data': image_batch075}, is_training=False, n_classes=n_classes)
    with tf.variable_scope('', reuse=True):
        net05 = DeepLabResNetModel({'data': image_batch05}, is_training=False, n_classes=n_classes)

    # Which variables to load.
    restore_var = tf.global_variables()
    
    # Predictions.
    raw_output100 = net.layers['fc1_voc12']
    raw_output075 = tf.image.resize_images(net075.layers['fc1_voc12'], tf.shape(raw_output100)[1:3,])
    raw_output05 = tf.image.resize_images(net05.layers['fc1_voc12'], tf.shape(raw_output100)[1:3,])
    
    raw_output = tf.reduce_max(tf.stack([raw_output100, raw_output075, raw_output05]), axis=0)
    raw_output = tf.image.resize_bilinear(raw_output, tf.shape(image_batch)[1:3,])
    raw_output = tf.argmax(raw_output, dimension=3)
    pred = tf.expand_dims(raw_output, dim=3) # Create 4-d tensor.
    
    # mIoU
    preds = tf.reshape(pred, [-1,])
    gt = tf.reshape(label_batch, [-1,])
    weights = tf.cast(tf.less_equal(gt, n_classes - 1), tf.int32) # Ignoring all labels greater than or equal to n_classes.
    mIoU, update_op_iou = tf.contrib.metrics.streaming_mean_iou(preds, gt, num_classes=n_classes, weights=weights)
    macc, update_op_acc = tf.contrib.metrics.streaming_accuracy(preds, gt, weights=weights)
    
    # Set up tf session and initialize variables. 
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    sess = tf.Session(config=config)
    init = tf.global_variables_initializer()
    
    sess.run(init)
    sess.run(tf.local_variables_initializer())
    
    # Load weights.
    loader = tf.train.Saver(var_list=restore_var)
    if args.restore_from is not None:
        if load(loader, sess, args.restore_from):
            print(" [*] Load SUCCESS")
        else:
            print(" [!] Load failed...")
    
    # Start queue threads.
    threads = tf.train.start_queue_runners(coord=coord, sess=sess)
    
    # Read test images list
    list_file = open('./dataset/human/list/val_id.txt', 'r')
    list_line = list_file.readlines()

    # Iterate over training steps.
    for step in range(args.num_steps):
        predict_, groundtruth_, _, _ = sess.run([pred, label_batch, update_op_iou, update_op_acc])
        if step % 100 == 0:
            print('step {:d}'.format(step))
            print (list_line[step][:-1])
        sio.savemat('./output/features/{}.mat'.format(list_line[step][:-1]), {'data': predict_[0,:,:,0]})
        # print (predict_.shape)
        # fig = plt.figure()
        # fig.add_subplot(1,2,1)
        # plt.imshow(predict_[0,:,:,0])
        # fig.add_subplot(1,2,2)
        # plt.imshow(groundtruth_[0,:,:,0])
        # plt.show()
    print('Mean IoU: {:.3f},   Mean Acc: {:.3f}'.format(mIoU.eval(session=sess), macc.eval(session=sess)))
    coord.request_stop()
    coord.join(threads)
    
if __name__ == '__main__':
    main()
