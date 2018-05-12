# --------------------------------------------------------
# Deformable Convolutional Networks
# Copyright (c) 2016 by Contributors
# Copyright (c) 2017 Microsoft
# Licensed under The Apache-2.0 License [see LICENSE for details]
# Modified by Yuwen Xiong
# --------------------------------------------------------

"""
Proposal Target Operator selects foreground and background roi and assigns label, bbox_transform to them.
"""

import mxnet as mx
import numpy as np
from distutils.util import strtobool
from easydict import EasyDict as edict
import cPickle


from core.rcnn import sample_rois, sample_rois_v2

DEBUG = False


class ProposalTargetPosOperator(mx.operator.CustomOp):
    def __init__(self, num_classes, batch_images, batch_rois, cfg, fg_fraction, feat_dim=1024):
        super(ProposalTargetPosOperator, self).__init__()
        self._num_classes = num_classes
        self._batch_images = batch_images
        self._batch_rois = batch_rois
        self._cfg = cfg
        self._fg_fraction = fg_fraction
        self._feat_dim = feat_dim
        if DEBUG:
            self._count = 0
            self._fg_num = 0
            self._bg_num = 0

    def forward(self, is_train, req, in_data, out_data, aux):
        assert self._batch_rois == -1 or self._batch_rois % self._batch_images == 0, \
            'batchimages {} must devide batch_rois {}'.format(self._batch_images, self._batch_rois)
        all_rois = in_data[0].asnumpy()
        gt_boxes = in_data[1].asnumpy()

        if self._batch_rois == -1:
            rois_per_image = all_rois.shape[0] + gt_boxes.shape[0]
            fg_rois_per_image = rois_per_image
        elif self._batch_rois == -2:
            rois_per_image = all_rois.shape[0]
            fg_rois_per_image = rois_per_image
        elif self._batch_rois < -10:
            rois_per_image = -self._batch_rois / self._batch_images
            fg_rois_per_image = np.round(self._fg_fraction * rois_per_image).astype(int)
        else:
            rois_per_image = self._batch_rois / self._batch_images
            fg_rois_per_image = np.round(self._fg_fraction * rois_per_image).astype(int)


        # Include ground-truth boxes in the set of candidate rois
        zeros = np.zeros((gt_boxes.shape[0], 1), dtype=gt_boxes.dtype)
        if self._batch_rois >= -1:
            all_rois = np.vstack((all_rois, np.hstack((zeros, gt_boxes[:, :-1]))))
        # Sanity check: single batch only
        assert np.all(all_rois[:, 0] == 0), 'Only single item batches are supported'

        if self._batch_rois == -1 or self._batch_rois == -2:
            #rois, labels, bbox_targets, bbox_weights = \
            #    sample_rois(all_rois, fg_rois_per_image, rois_per_image, self._num_classes, self._cfg, gt_boxes=gt_boxes)
            rois, labels, bbox_targets, bbox_weights = \
                sample_rois_v2(all_rois, self._num_classes, self._cfg, gt_boxes=gt_boxes)
        else:
            rois, labels, bbox_targets, bbox_weights = \
                sample_rois(all_rois, fg_rois_per_image, rois_per_image, self._num_classes, self._cfg, gt_boxes=gt_boxes)

        rois_center = rois[:,1:4:2]+rois[:,2:5:2]
        rois_wh = rois[:,3:5] - rois[:,2:4] + 1
        #position_feat = np.zeros((len(labels), feat_dim), dtype='float32')
        dim_mat = np.tile(np.arange(self._feat_dim/8),[len(labels), 1])
        dim_mat = 10000.0 ** (8.0/self._feat_dim * dim_mat)
        x_mat = np.transpose(np.tile(rois_center[:,0]/64.0,[self._feat_dim/8, 1]))
        y_mat = np.transpose(np.tile(rois_center[:,1]/64.0,[self._feat_dim/8, 1]))
        w_mat = np.transpose(np.tile(rois_wh[:,0]/6.4,[self._feat_dim/8, 1]))
        h_mat = np.transpose(np.tile(rois_wh[:,1]/6.4,[self._feat_dim/8, 1]))
        position_feat = np.hstack((np.sin(x_mat / dim_mat), np.cos(x_mat/dim_mat), np.sin(y_mat/dim_mat), np.cos(y_mat/dim_mat), 
                np.sin(w_mat / dim_mat), np.cos(w_mat/dim_mat), np.sin(h_mat/dim_mat), np.cos(h_mat/dim_mat)))
 
        if DEBUG:
            print "labels=", labels
            print 'num fg: {}'.format((labels > 0).sum())
            print 'num bg: {}'.format((labels == 0).sum())
            self._count += 1
            self._fg_num += (labels > 0).sum()
            self._bg_num += (labels == 0).sum()
            print "self._count=", self._count
            print 'num fg avg: {}'.format(self._fg_num / self._count)
            print 'num bg avg: {}'.format(self._bg_num / self._count)
            print 'ratio: {:.3f}'.format(float(self._fg_num) / float(self._bg_num))

        for ind, val in enumerate([rois, labels, bbox_targets, bbox_weights, position_feat]):
            self.assign(out_data[ind], req[ind], val)

    def backward(self, req, out_grad, in_data, out_data, in_grad, aux):
        self.assign(in_grad[0], req[0], 0)
        self.assign(in_grad[1], req[1], 0)


@mx.operator.register('proposal_target_pos')
class ProposalTargetPosProp(mx.operator.CustomOpProp):
    def __init__(self, num_classes, batch_images, batch_rois, cfg, fg_fraction='0.25', feat_dim=1024):
        super(ProposalTargetPosProp, self).__init__(need_top_grad=False)
        self._num_classes = int(num_classes)
        self._batch_images = int(batch_images)
        self._batch_rois = int(batch_rois)
        self._cfg = cPickle.loads(cfg)
        self._fg_fraction = float(fg_fraction)
        self._feat_dim = int(feat_dim)

    def list_arguments(self):
        return ['rois', 'gt_boxes']

    def list_outputs(self):
        return ['rois_output', 'label', 'bbox_target', 'bbox_weight', 'position_feat']

    def infer_shape(self, in_shape):
        rpn_rois_shape = in_shape[0]
        gt_boxes_shape = in_shape[1]

        if self._batch_rois == -1:
            rois = rpn_rois_shape[0] + gt_boxes_shape[0]
        elif self._batch_rois == -2:
            rois = rpn_rois_shape[0]
        elif self._batch_rois < -10:
            rois = -self._batch_rois
        else:
            rois = self._batch_rois

        #rois = rpn_rois_shape[0] + gt_boxes_shape[0] if self._batch_rois == -1 else self._batch_rois

        output_rois_shape = (rois, 5)
        label_shape = (rois, )
        bbox_target_shape = (rois, self._num_classes * 4)
        bbox_weight_shape = (rois, self._num_classes * 4)
        position_feat_shape = (rois, self._feat_dim)

        return [rpn_rois_shape, gt_boxes_shape], \
               [output_rois_shape, label_shape, bbox_target_shape, bbox_weight_shape, position_feat_shape]

    def create_operator(self, ctx, shapes, dtypes):
        return ProposalTargetPosOperator(self._num_classes, self._batch_images, self._batch_rois, self._cfg, self._fg_fraction, self._feat_dim)

    def declare_backward_dependency(self, out_grad, in_data, out_data):
        return []
