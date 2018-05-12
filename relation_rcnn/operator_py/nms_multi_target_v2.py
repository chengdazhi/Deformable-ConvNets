# --------------------------------------------------------
# Deformable Convolutional Networks
# Copyright (c) 2016 by Contributors
# Copyright (c) 2017 Microsoft
# Licensed under The Apache-2.0 License [see LICENSE for details]
# Modified by Jiayuan Gu
# --------------------------------------------------------

"""
Nms Target Operator selects foreground and background roi and assigns label, bbox_transform to them.
"""

import mxnet as mx
import numpy as np

from bbox.bbox_transform import bbox_overlaps

DEBUG = False


class NmsMultiTargetOp(mx.operator.CustomOp):
    def __init__(self, target_thresh):
        super(NmsMultiTargetOp, self).__init__()
        self._target_thresh = target_thresh
        self._num_thresh = len(target_thresh)

    def forward(self, is_train, req, in_data, out_data, aux):
        # bbox, [first_n, num_fg_classes, 4]
        bbox = in_data[0].asnumpy()
        num_boxes = bbox.shape[0]
        num_fg_classes = bbox.shape[1]
        gt_box = in_data[1].asnumpy()
        # score, [first_n, num_fg_classes]
        score = in_data[2].asnumpy()

        batch_image, num_gt, code_size = gt_box.shape
        assert batch_image == 1, 'only support batch_image=1, but receive %d' % num_gt
        assert code_size == 5, 'code_size of gt should be 5, but receive %d' % code_size
        assert len(score.shape) == 2, 'shape of score is %d instead of 2.' % len(score.shape)
        assert score.shape[1] == num_fg_classes, 'number of fg classes should be same for boxes and scores'

        output_list = []
        for cls_idx in range(0, num_fg_classes):
            valid_gt_mask = (gt_box[0, :, -1].astype(np.int32)==(cls_idx+1))
            valid_gt_box = gt_box[0, valid_gt_mask, :]
            num_valid_gt = len(valid_gt_box)

            if num_valid_gt == 0:
               output = np.zeros(shape=(num_boxes, self._num_thresh), dtype=np.bool_)
               output_list.append(output)
            else:
                bbox_per_class = bbox[:, cls_idx, :]
                overlap_mat = bbox_overlaps(bbox_per_class.astype(np.float),
                                            valid_gt_box[:,:-1].astype(np.float))
                output_list_per_class = []
                for thresh in self._target_thresh:
                    # following mAP metric
                    num_matched = 0
                    box_matched = np.zeros((num_boxes,), dtype=np.bool_)
                    gt_matched = np.zeros((num_valid_gt,), dtype=np.bool_)
                    for box_idx in range(num_boxes):
                        if num_matched >= num_valid_gt:
                            break
                        max_overlap = thresh
                        matched = -1
                        box_ovr = overlap_mat[box_idx, :]
                        for gt_idx in range(num_valid_gt):
                            ovr = box_ovr[gt_idx]
                            # if gt is already matched, continue
                            if gt_matched[gt_idx]:
                                continue
                            # record better match
                            if ovr > max_overlap:
                                max_overlap = ovr
                                matched = gt_idx
                        if matched > -1:
                            box_matched[box_idx] = 1
                            gt_matched[matched] = 1
                            num_matched += 1
                    output_list_per_class.append(box_matched)
                output_per_class = np.stack(output_list_per_class, axis=-1)
                output_list.append(output_per_class)
        blob = np.stack(output_list, axis=1).astype(np.float32, copy=False)
        self.assign(out_data[0], req[0], blob)

    def backward(self, req, out_grad, in_data, out_data, in_grad, aux):
        self.assign(in_grad[0], req[0], 0)
        self.assign(in_grad[1], req[1], 0)
        self.assign(in_grad[2], req[2], 0)


@mx.operator.register("nms_multi_target_v2")
class NmsMultiTargetProp(mx.operator.CustomOpProp):
    def __init__(self, target_thresh):
        super(NmsMultiTargetProp, self).__init__(need_top_grad=False)
        self._target_thresh = np.fromstring(target_thresh[1:-1], dtype=float, sep=' ')
        self._num_thresh = len(self._target_thresh)

    def list_arguments(self):
        return ['bbox', 'gt_bbox', 'score']

    def list_outputs(self):
        return ['nms_multi_target']

    def infer_shape(self, in_shape):
        bbox_shape = in_shape[0]
        # gt_box_shape = in_shape[1]
        score_shape = in_shape[2]

        assert bbox_shape[0] == score_shape[0], 'ROI number should be same for bbox and score'

        num_boxes = bbox_shape[0]
        num_fg_classes = bbox_shape[1]
        output_shape = (num_boxes, num_fg_classes, self._num_thresh)

        return in_shape, [output_shape]

    def create_operator(self, ctx, shapes, dtypes):
        return NmsMultiTargetOp(self._target_thresh)

    def declare_backward_dependency(self, out_grad, in_data, out_data):
        return []
